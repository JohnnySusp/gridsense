from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.db.neo4j import Neo4jStore, get_neo4j
from api.models.graph import GridNode, GridNodeCreate, GridPath, GridRelationship, GridRelationshipCreate

router = APIRouter(prefix="/grid", tags=["Grid Topology"])

_IDENTIFIER_EXPRESSION = "coalesce(n.node_id, n.asset_id, n.meter_id, n.gsp_id)"
_RELATIONSHIP_TYPES = "FEEDS|SUPPLIES|CONNECTS_TO"


def _node_identity(alias: str) -> str:
    return f"coalesce({alias}.node_id, {alias}.asset_id, {alias}.meter_id, {alias}.gsp_id)"


async def _node_exists(neo4j: Neo4jStore, node_id: str) -> bool:
    rows = await neo4j.read(
        f"""
        MATCH (n)
        WHERE {_IDENTIFIER_EXPRESSION} = $node_id
        RETURN count(n) AS count
        """,
        node_id=node_id,
    )
    return bool(rows and rows[0]["count"] > 0)


@router.get("/ping")
async def grid_ping() -> dict[str, str]:
    return {"router": "grid", "status": "ok"}


@router.get(
    "/nodes",
    response_model=list[GridNode],
    summary="List grid nodes, optionally filtered by Neo4j label",
)
async def list_nodes(
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
    label: Annotated[str | None, Query(description="Example: Substation, Transformer, SmartMeter")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    rows = await neo4j.read(
        f"""
        MATCH (n)
        WHERE $label IS NULL OR $label IN labels(n)
        RETURN {_IDENTIFIER_EXPRESSION} AS node_id,
               labels(n) AS labels,
               properties(n) AS properties
        ORDER BY node_id
        LIMIT $limit
        """,
        label=label,
        limit=limit,
    )
    return rows


@router.post(
    "/nodes",
    response_model=GridNode,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new node to the Neo4j topology graph",
)
async def create_node(
    payload: GridNodeCreate,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> dict[str, Any]:
    labels_clause = ":".join(payload.labels)
    properties = dict(payload.properties)
    properties["node_id"] = payload.node_id

    rows = await neo4j.write(
        f"""
        MERGE (n {{node_id: $node_id}})
        SET n += $properties
        SET n:{labels_clause}
        RETURN {_IDENTIFIER_EXPRESSION} AS node_id,
               labels(n) AS labels,
               properties(n) AS properties
        """,
        node_id=payload.node_id,
        properties=properties,
    )
    return rows[0]


@router.post(
    "/relationships",
    response_model=GridRelationship,
    status_code=status.HTTP_201_CREATED,
    summary="Add a new relationship between two Neo4j topology nodes",
)
async def create_relationship(
    payload: GridRelationshipCreate,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> dict[str, Any]:
    relationship_type = payload.relationship_type
    rows = await neo4j.write(
        f"""
        MATCH (source), (target)
        WHERE {_node_identity("source")} = $source_id
          AND {_node_identity("target")} = $target_id
        MERGE (source)-[r:{relationship_type}]->(target)
        SET r += $properties
        RETURN {_node_identity("source")} AS source,
               {_node_identity("target")} AS target,
               type(r) AS type,
               properties(r) AS properties
        """,
        source_id=payload.source_id,
        target_id=payload.target_id,
        properties=payload.properties,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Source or target topology node not found")
    return rows[0]


@router.get(
    "/nodes/{node_id}",
    response_model=GridNode,
    summary="Get one grid node by node_id, asset_id, meter_id, or gsp_id",
)
async def get_node(
    node_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> dict[str, Any]:
    rows = await neo4j.read(
        f"""
        MATCH (n)
        WHERE {_IDENTIFIER_EXPRESSION} = $node_id
        RETURN {_IDENTIFIER_EXPRESSION} AS node_id, labels(n) AS labels, properties(n) AS properties
        LIMIT 1
        """,
        node_id=node_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Grid node not found")
    return rows[0]


@router.get(
    "/substations",
    response_model=list[GridNode],
    summary="List substations",
)
async def list_substations(
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> list[dict[str, Any]]:
    return await neo4j.read(
        f"""
        MATCH (n:Substation)
        RETURN {_IDENTIFIER_EXPRESSION} AS node_id, labels(n) AS labels, properties(n) AS properties
        ORDER BY node_id
        """
    )


@router.get(
    "/feeders",
    response_model=list[GridRelationship],
    summary="List GridSupplyPoint -> Substation feeder relationships",
)
async def list_feeders(
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> list[dict[str, Any]]:
    return await neo4j.read(
        f"""
        MATCH (g:GridSupplyPoint)-[r:FEEDS]->(s:Substation)
        RETURN {_node_identity("g")} AS source,
               {_node_identity("s")} AS target,
               type(r) AS type,
               properties(r) AS properties
        ORDER BY r.feeder_id
        """
    )


@router.get(
    "/fault-impact/{node_id}",
    response_model=list[GridNode],
    summary="Return downstream nodes affected by a fault at node_id",
)
async def fault_impact(
    node_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
    max_depth: Annotated[
        int,
        Query(ge=1, le=10, description="Maximum downstream traversal depth in graph hops"),
    ] = 6,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict[str, Any]]:
    if not await _node_exists(neo4j, node_id):
        raise HTTPException(status_code=404, detail="Fault node not found")

    cypher = f"""
        MATCH (failed)
        WHERE {_node_identity("failed")} = $node_id
        MATCH (failed)-[:{_RELATIONSHIP_TYPES}*1..{max_depth}]->(affected)
        RETURN DISTINCT {_node_identity("affected")} AS node_id,
               labels(affected) AS labels,
               properties(affected) AS properties
        ORDER BY node_id
        LIMIT $limit
    """
    return await neo4j.read(cypher, node_id=node_id, limit=limit)


@router.get(
    "/restore-paths/{node_id}",
    response_model=list[GridPath],
    summary="Return alternative supply paths that avoid a failed node",
)
async def restore_paths(
    node_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
    impact_depth: Annotated[int, Query(ge=1, le=10)] = 6,
    path_depth: Annotated[int, Query(ge=1, le=15)] = 10,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[dict[str, Any]]:
    if not await _node_exists(neo4j, node_id):
        raise HTTPException(status_code=404, detail="Failed node not found")

    # Find downstream affected nodes, then look for a supply path from any GSP
    # to those affected nodes that does not pass through the failed node.
    cypher = f"""
        MATCH (failed)
        WHERE {_node_identity("failed")} = $node_id
        MATCH (failed)-[:{_RELATIONSHIP_TYPES}*1..{impact_depth}]->(affected)
        MATCH path = (g:GridSupplyPoint)-[:{_RELATIONSHIP_TYPES}*1..{path_depth}]->(affected)
        WHERE NONE(n IN nodes(path) WHERE n = failed)
        RETURN DISTINCT [n IN nodes(path) | {{
                  node_id: {_node_identity("n")},
                  labels: labels(n),
                  properties: properties(n)
               }}] AS nodes,
               [r IN relationships(path) | {{
                  source: {_node_identity("startNode(r)")},
                  target: {_node_identity("endNode(r)")},
                  type: type(r),
                  properties: properties(r)
               }}] AS relationships
        LIMIT $limit
    """
    return await neo4j.read(cypher, node_id=node_id, limit=limit)


@router.get(
    "/nodes/{node_id}/downstream",
    response_model=list[GridPath],
    summary="Traverse downstream from a grid node",
)
async def downstream_paths(
    node_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
    depth: Annotated[int, Query(ge=1, le=6)] = 3,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> list[dict[str, Any]]:
    # Neo4j variable-length relationship bounds cannot be parameterized, so the
    # validated integer is safely interpolated here.
    cypher = f"""
        MATCH path = (start)-[*1..{depth}]->(end)
        WHERE {_node_identity("start")} = $node_id
        RETURN [n IN nodes(path) | {{
                  node_id: {_node_identity("n")},
                  labels: labels(n),
                  properties: properties(n)
               }}] AS nodes,
               [r IN relationships(path) | {{
                  source: {_node_identity("startNode(r)")},
                  target: {_node_identity("endNode(r)")},
                  type: type(r),
                  properties: properties(r)
               }}] AS relationships
        LIMIT $limit
    """
    rows = await neo4j.read(cypher, node_id=node_id, limit=limit)
    if not rows:
        raise HTTPException(status_code=404, detail="No downstream paths found for this node")
    return rows


@router.get(
    "/meters/{meter_id}/upstream",
    response_model=GridPath,
    summary="Return the supply path from GSP to a smart meter",
)
async def meter_upstream_path(
    meter_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> dict[str, Any]:
    rows = await neo4j.read(
        f"""
        MATCH path = (g:GridSupplyPoint)-[:{_RELATIONSHIP_TYPES}*1..10]->(m:SmartMeter {{meter_id: $meter_id}})
        RETURN [n IN nodes(path) | {{
                  node_id: {_node_identity("n")},
                  labels: labels(n),
                  properties: properties(n)
               }}] AS nodes,
               [r IN relationships(path) | {{
                  source: {_node_identity("startNode(r)")},
                  target: {_node_identity("endNode(r)")},
                  type: type(r),
                  properties: properties(r)
               }}] AS relationships
        LIMIT 1
        """,
        meter_id=meter_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Meter path not found")
    return rows[0]
