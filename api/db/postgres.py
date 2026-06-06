from __future__ import annotations

import json

import asyncpg
from asyncpg import Pool
from fastapi import Request

from api.config import settings


async def _init_connection(connection: asyncpg.Connection) -> None:
    await connection.set_type_codec(
        "json",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )
    await connection.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


class PostgresStore:
    def __init__(self) -> None:
        self.pool: Pool | None = None

    async def connect(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=settings.postgres_dsn,
            min_size=1,
            max_size=10,
            init=_init_connection,
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    def require_pool(self) -> Pool:
        if self.pool is None:
            raise RuntimeError("PostgreSQL connection has not been initialized")
        return self.pool


def get_postgres(request: Request) -> PostgresStore:
    return request.app.state.postgres
