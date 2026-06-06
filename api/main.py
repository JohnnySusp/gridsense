from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from prometheus_fastapi_instrumentator import Instrumentator

from api.db.cassandra import CassandraStore
from api.db.mongo import MongoStore
from api.db.neo4j import Neo4jStore
from api.db.postgres import PostgresStore
from api.db.redis import RedisStore
from api.routers import alerts, billing, equipment, grid, sensors


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.cassandra = CassandraStore()
    app.state.neo4j = Neo4jStore()
    app.state.mongo = MongoStore()
    app.state.postgres = PostgresStore()
    app.state.redis = RedisStore()

    try:
        await app.state.cassandra.connect()
        await app.state.neo4j.connect()
        await app.state.mongo.connect()
        await app.state.postgres.connect()
        await app.state.redis.connect()
        yield
    finally:
        await app.state.redis.close()
        await app.state.postgres.close()
        await app.state.mongo.close()
        await app.state.neo4j.close()
        await app.state.cassandra.close()


app = FastAPI(title="GridSense API", version="0.2.0", lifespan=lifespan)

app.include_router(sensors.router)
app.include_router(grid.router)
app.include_router(equipment.router)
app.include_router(billing.router)
app.include_router(alerts.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "gridsense-api"}


Instrumentator().instrument(app).expose(app, endpoint="/metrics")
