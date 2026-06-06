from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated
from uuid import uuid1
from zlib import crc32

from fastapi import APIRouter, Depends, Query, status

from api.db.cassandra import CassandraStore, get_cassandra
from api.models.cassandra import (
    RelayEventCreate,
    RelayEventOut,
    SensorReadingCreate,
    SensorReadingOut,
    TimeBucketReading,
    normalize_cassandra_row,
)

router = APIRouter(prefix="/sensors", tags=["Sensors"])


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _hour_bucket(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _sensor_shard(sensor_id: str, shards: int = 16) -> int:
    return crc32(sensor_id.encode("utf-8")) % shards


@router.get("/ping")
async def sensors_ping() -> dict[str, str]:
    return {"router": "sensors", "status": "ok"}


@router.post(
    "/readings",
    response_model=SensorReadingOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store one sensor reading in Cassandra",
)
async def create_reading(
    payload: SensorReadingCreate,
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
) -> SensorReadingOut:
    reading_time = payload.reading_time or _utc_now()
    reading_id = uuid1()
    bucket_day = reading_time.date()
    bucket_start = _hour_bucket(reading_time)
    shard = _sensor_shard(payload.sensor_id)

    await cassandra.execute(
        """
        INSERT INTO sensor_readings (
          sensor_id, sensor_type, bucket_day, reading_time, reading_id,
          voltage, current, power_factor, temperature, quality_flag
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            payload.sensor_id,
            payload.sensor_type,
            bucket_day,
            reading_time,
            reading_id,
            payload.voltage,
            payload.current,
            payload.power_factor,
            payload.temperature,
            payload.quality_flag,
        ),
    )
    await cassandra.execute(
        """
        INSERT INTO sensor_readings_by_bucket (
          bucket_start, shard, sensor_id, sensor_type, reading_time, reading_id,
          voltage, current, power_factor, temperature, quality_flag
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            bucket_start,
            shard,
            payload.sensor_id,
            payload.sensor_type,
            reading_time,
            reading_id,
            payload.voltage,
            payload.current,
            payload.power_factor,
            payload.temperature,
            payload.quality_flag,
        ),
    )

    return SensorReadingOut(
        **payload.model_dump(exclude={"reading_time"}),
        reading_time=reading_time,
        bucket_day=bucket_day,
        reading_id=reading_id,
    )


@router.get(
    "/{sensor_id}/readings",
    response_model=list[SensorReadingOut],
    summary="Read recent readings for one sensor and Cassandra day bucket",
)
async def list_sensor_readings(
    sensor_id: str,
    day: Annotated[date, Query(description="Cassandra partition day, e.g. 2026-06-05")],
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    rows = await cassandra.execute(
        """
        SELECT sensor_id, sensor_type, bucket_day, reading_time, reading_id,
               voltage, current, power_factor, temperature, quality_flag
        FROM sensor_readings
        WHERE sensor_id = %s AND bucket_day = %s
        LIMIT %s
        """,
        (sensor_id, day, limit),
    )
    return [normalize_cassandra_row(row) for row in rows]


@router.get(
    "/readings/by-bucket",
    response_model=list[TimeBucketReading],
    summary="Read a global time bucket shard for dashboard-style scans",
)
async def list_bucket_readings(
    bucket_start: Annotated[datetime, Query(description="Hour bucket start, e.g. 2026-06-05T13:00:00Z")],
    shard: Annotated[int, Query(ge=0, le=15)],
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    rows = await cassandra.execute(
        """
        SELECT bucket_start, shard, sensor_id, sensor_type, reading_time, reading_id,
               voltage, current, power_factor, temperature, quality_flag
        FROM sensor_readings_by_bucket
        WHERE bucket_start = %s AND shard = %s
        LIMIT %s
        """,
        (bucket_start, shard, limit),
    )
    return [normalize_cassandra_row(row) for row in rows]


@router.post(
    "/relays/events",
    response_model=RelayEventOut,
    status_code=status.HTTP_201_CREATED,
    summary="Store a relay event in Cassandra",
)
async def create_relay_event(
    payload: RelayEventCreate,
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
) -> RelayEventOut:
    event_time = uuid1()
    await cassandra.execute(
        """
        INSERT INTO relay_events (
          feeder_id, event_time, relay_id, event_type, fault_type, current_ka
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            payload.feeder_id,
            event_time,
            payload.relay_id,
            payload.event_type,
            payload.fault_type,
            payload.current_ka,
        ),
    )
    return RelayEventOut(**payload.model_dump(), event_time=event_time)


@router.get(
    "/relays/{feeder_id}/events",
    response_model=list[RelayEventOut],
    summary="List relay events for a feeder",
)
async def list_relay_events(
    feeder_id: str,
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[dict]:
    rows = await cassandra.execute(
        """
        SELECT feeder_id, event_time, relay_id, event_type, fault_type, current_ka
        FROM relay_events
        WHERE feeder_id = %s
        LIMIT %s
        """,
        (feeder_id, limit),
    )
    return [normalize_cassandra_row(row) for row in rows]
