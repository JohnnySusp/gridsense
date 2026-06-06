from __future__ import annotations

from typing import Any, Sequence

from cassandra.cluster import Cluster, Session
from cassandra.query import dict_factory
from fastapi import Request
from fastapi.concurrency import run_in_threadpool

from api.config import settings


class CassandraStore:
    """Thin async-friendly wrapper around the cassandra-driver sync Session."""

    def __init__(self) -> None:
        self.cluster: Cluster | None = None
        self.session: Session | None = None

    async def connect(self) -> None:
        def _connect() -> tuple[Cluster, Session]:
            cluster = Cluster([settings.cassandra_host])
            session = cluster.connect(settings.cassandra_keyspace)
            session.row_factory = dict_factory
            return cluster, session

        self.cluster, self.session = await run_in_threadpool(_connect)

    async def close(self) -> None:
        if self.cluster is not None:
            await run_in_threadpool(self.cluster.shutdown)
            self.cluster = None
            self.session = None

    async def execute(self, query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
        if self.session is None:
            raise RuntimeError("Cassandra connection has not been initialized")

        def _execute() -> list[dict[str, Any]]:
            result = self.session.execute(query, tuple(params or ()))
            return list(result)

        return await run_in_threadpool(_execute)


def get_cassandra(request: Request) -> CassandraStore:
    return request.app.state.cassandra
