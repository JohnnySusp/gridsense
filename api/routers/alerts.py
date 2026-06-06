from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator
from uuid import uuid4

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from api.db.redis import RedisStore, get_redis
from api.models.alerts import AlertCreate, AlertOut

router = APIRouter(prefix="/alerts", tags=["Alerts"])

ALERT_CHANNEL = "gridsense:alerts"
RECENT_ALERTS_KEY = "gridsense:alerts:recent"
ACTIVE_ALERTS_KEY = "gridsense:alerts:active"
MAX_RECENT_ALERTS = 500
MAX_ACTIVE_ALERTS = 500


@router.get("/ping")
async def alerts_ping() -> dict[str, str]:
    return {"router": "alerts", "status": "ok"}


@router.post(
    "/publish",
    response_model=AlertOut,
    status_code=status.HTTP_201_CREATED,
    summary="Publish a new fault alert to Redis subscribers",
)
@router.post(
    "",
    response_model=AlertOut,
    status_code=status.HTTP_201_CREATED,
    summary="Publish an alert through Redis Pub/Sub and keep a short recent history",
)
async def publish_alert(
    payload: AlertCreate,
    redis: Annotated[RedisStore, Depends(get_redis)],
) -> dict:
    client = redis.require_client()
    alert = AlertOut(
        **payload.model_dump(),
        alert_id=uuid4(),
        created_at=datetime.now(timezone.utc),
    ).model_dump(mode="json")
    encoded = json.dumps(alert)

    pipe = client.pipeline(transaction=False)
    pipe.lpush(RECENT_ALERTS_KEY, encoded)
    pipe.ltrim(RECENT_ALERTS_KEY, 0, MAX_RECENT_ALERTS - 1)
    if alert.get("active", True):
        pipe.lpush(ACTIVE_ALERTS_KEY, encoded)
        pipe.ltrim(ACTIVE_ALERTS_KEY, 0, MAX_ACTIVE_ALERTS - 1)
    pipe.publish(ALERT_CHANNEL, encoded)
    await pipe.execute()

    return alert


@router.get(
    "/active",
    response_model=list[AlertOut],
    summary="Read active fault alerts kept in Redis",
)
async def active_alerts(
    redis: Annotated[RedisStore, Depends(get_redis)],
    limit: Annotated[int, Query(ge=1, le=MAX_ACTIVE_ALERTS)] = 100,
) -> list[dict]:
    client = redis.require_client()
    items = await client.lrange(ACTIVE_ALERTS_KEY, 0, limit - 1)
    return [json.loads(item) for item in items]


@router.get(
    "/recent",
    response_model=list[AlertOut],
    summary="Read recent alerts kept in Redis",
)
async def recent_alerts(
    redis: Annotated[RedisStore, Depends(get_redis)],
    limit: Annotated[int, Query(ge=1, le=MAX_RECENT_ALERTS)] = 50,
) -> list[dict]:
    client = redis.require_client()
    items = await client.lrange(RECENT_ALERTS_KEY, 0, limit - 1)
    return [json.loads(item) for item in items]


@router.get(
    "/stream",
    summary="Server-Sent Events stream for live alerts",
)
async def stream_alerts(redis: Annotated[RedisStore, Depends(get_redis)]) -> StreamingResponse:
    client = redis.require_client()

    async def events() -> AsyncIterator[str]:
        pubsub = client.pubsub()
        await pubsub.subscribe(ALERT_CHANNEL)
        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=15)
                if message is None:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {message['data']}\n\n"
        finally:
            await pubsub.unsubscribe(ALERT_CHANNEL)
            await pubsub.aclose()

    return StreamingResponse(events(), media_type="text/event-stream")
