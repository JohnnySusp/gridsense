from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from fastapi import Request
from neo4j import AsyncDriver, AsyncGraphDatabase

from api.config import settings

def normalize_neo4j_value(value: Any) -> Any:
    """
    Convert Neo4j driver-specific values into JSON/Pydantic-friendly values.

    This prevents endpoints from failing when Neo4j returns values such as
    neo4j.time.Date inside node or relationship properties.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, (datetime, date, time)):
        return value.isoformat()

    if isinstance(value, timedelta):
        return value.total_seconds()

    if isinstance(value, dict):
        return {
            str(key): normalize_neo4j_value(inner_value)
            for key, inner_value in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [normalize_neo4j_value(item) for item in value]

    # Neo4j-specific temporal classes, for example neo4j.time.Date.
    if type(value).__module__.startswith("neo4j.time"):
        to_native = getattr(value, "to_native", None)
        if callable(to_native):
            try:
                return normalize_neo4j_value(to_native())
            except Exception:
                pass

        for method_name in ("iso_format", "isoformat"):
            method = getattr(value, method_name, None)
            if callable(method):
                try:
                    return method()
                except Exception:
                    pass

        return str(value)

    return value


def normalize_neo4j_record(record: dict[str, Any]) -> dict[str, Any]:
    return normalize_neo4j_value(record)

class Neo4jStore:
    def __init__(self) -> None:
        self.driver: AsyncDriver | None = None

    async def connect(self) -> None:
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        await self.driver.verify_connectivity()

    async def close(self) -> None:
        if self.driver is not None:
            await self.driver.close()
            self.driver = None

    async def read(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if self.driver is None:
            raise RuntimeError("Neo4j connection has not been initialized")
        async with self.driver.session() as session:
            result = await session.run(cypher, **params)
            return [normalize_neo4j_record(record.data()) async for record in result]

    async def write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if self.driver is None:
            raise RuntimeError("Neo4j connection has not been initialized")

        async def _work(tx):
            result = await tx.run(cypher, **params)
            return [normalize_neo4j_record(record.data()) async for record in result]

        async with self.driver.session() as session:
            return await session.execute_write(_work)


def get_neo4j(request: Request) -> Neo4jStore:
    return request.app.state.neo4j
