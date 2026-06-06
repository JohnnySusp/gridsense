from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from api.db.neo4j import Neo4jStore, get_neo4j
from api.models.graph import GridNode, GridPath, GridRelationship

router = APIRouter(prefix="/grid", tags=["Grid Topology"])


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
) -> list[dict]:
    rows = await neo4j.read(
        """
        MATCH (n)
        WHERE $label IS NULL OR $label IN labels(n)
        RETURN coalesce(n.node_id, n.asset_id, n.meter_id, n.gsp_id) AS node_id,
               labels(n) AS labels,
               properties(n) AS properties
        ORDER BY node_id
        LIMIT $limit
        """,
        label=label,
        limit=limit,
    )
    return rows


@router.get(
    "/nodes/{node_id}",
    response_model=GridNode,
    summary="Get one grid node by node_id",
)
async def get_node(
    node_id: str,
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> dict:
    rows = await neo4j.read(
        """
        MATCH (n {node_id: $node_id})
        RETURN n.node_id AS node_id, labels(n) AS labels, properties(n) AS properties
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
) -> list[dict]:
    return await neo4j.read(
        """
        MATCH (s:Substation)
        RETURN s.node_id AS node_id, labels(s) AS labels, properties(s) AS properties
        ORDER BY s.node_id
        """
    )


@router.get(
    "/feeders",
    response_model=list[GridRelationship],
    summary="List GridSupplyPoint -> Substation feeder relationships",
)
async def list_feeders(
    neo4j: Annotated[Neo4jStore, Depends(get_neo4j)],
) -> list[dict]:
    return await neo4j.read(
        """
        MATCH (g:GridSupplyPoint)-[r:FEEDS]->(s:Substation)
        RETURN g.node_id AS source,
               s.node_id AS target,
               type(r) AS type,
               properties(r) AS properties
        ORDER BY r.feeder_id
        """
    )


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
) -> list[dict]:
    # Neo4j variable-length relationship bounds cannot be parameterized, so the
    # validated integer is safely interpolated here.
    cypher = f"""
        MATCH path = (start {{node_id: $node_id}})-[*1..{depth}]->(end)
        RETURN [n IN nodes(path) | {{
                  node_id: n.node_id,
                  labels: labels(n),
                  properties: properties(n)
               }}] AS nodes,
               [r IN relationships(path) | {{
                  source: startNode(r).node_id,
                  target: endNode(r).node_id,
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
) -> dict:
    rows = await neo4j.read(
        """
        MATCH path = (g:GridSupplyPoint)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..10]->(m:SmartMeter {meter_id: $meter_id})
        RETURN [n IN nodes(path) | {
                  node_id: n.node_id,
                  labels: labels(n),
                  properties: properties(n)
               }] AS nodes,
               [r IN relationships(path) | {
                  source: startNode(r).node_id,
                  target: endNode(r).node_id,
                  type: type(r),
                  properties: properties(r)
               }] AS relationships
        LIMIT 1
        """,
        meter_id=meter_id,
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Meter path not found")
    return rows[0]
