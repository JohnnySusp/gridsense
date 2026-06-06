from __future__ import annotations

from fastapi import Request
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCollection, AsyncIOMotorDatabase

from api.config import settings


class MongoStore:
    def __init__(self) -> None:
        self.client: AsyncIOMotorClient | None = None
        self.db: AsyncIOMotorDatabase | None = None

    async def connect(self) -> None:
        self.client = AsyncIOMotorClient(settings.mongo_uri)
        self.db = self.client[settings.mongo_db]
        await self.client.admin.command("ping")

    async def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
            self.db = None

    @property
    def equipment(self) -> AsyncIOMotorCollection:
        if self.db is None:
            raise RuntimeError("MongoDB connection has not been initialized")
        return self.db["equipment"]


def get_mongo(request: Request) -> MongoStore:
    return request.app.state.mongo
