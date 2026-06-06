from __future__ import annotations

from fastapi import Request
from redis.asyncio import Redis

from api.config import settings


class RedisStore:
    def __init__(self) -> None:
        self.client: Redis | None = None

    async def connect(self) -> None:
        self.client = Redis.from_url(settings.redis_url, decode_responses=True)
        await self.client.ping()

    async def close(self) -> None:
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    def require_client(self) -> Redis:
        if self.client is None:
            raise RuntimeError("Redis connection has not been initialized")
        return self.client


def get_redis(request: Request) -> RedisStore:
    return request.app.state.redis
