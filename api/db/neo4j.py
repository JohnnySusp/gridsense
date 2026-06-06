from __future__ import annotations

from typing import Any

from fastapi import Request
from neo4j import AsyncDriver, AsyncGraphDatabase

from api.config import settings


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
            return [record.data() async for record in result]

    async def write(self, cypher: str, **params: Any) -> list[dict[str, Any]]:
        if self.driver is None:
            raise RuntimeError("Neo4j connection has not been initialized")

        async def _work(tx):
            result = await tx.run(cypher, **params)
            return [record.data() async for record in result]

        async with self.driver.session() as session:
            return await session.execute_write(_work)


def get_neo4j(request: Request) -> Neo4jStore:
    return request.app.state.neo4j
