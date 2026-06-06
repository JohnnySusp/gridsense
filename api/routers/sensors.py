from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from statistics import mean
from typing import Annotated, Any
from uuid import uuid1
from zlib import crc32

from fastapi import APIRouter, Depends, Query, status

from api.db.cassandra import CassandraStore, get_cassandra
from api.db.redis import RedisStore, get_redis
from api.models.cassandra import (
    RelayEventCreate,
    RelayEventOut,
    SensorReadingCreate,
    SensorReadingOut,
    SensorStats,
    SensorSummary,
    TimeBucketReading,
    normalize_cassandra_row,
)

router = APIRouter(prefix="/sensors", tags=["Sensors"])

SUMMARY_CACHE_TTL_SECONDS = 30
SUMMARY_CACHE_KEY_TEMPLATE = "gridsense:sensors:{sensor_id}:summary"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hour_bucket(value: datetime) -> datetime:
    return _as_utc(value).replace(minute=0, second=0, microsecond=0)


def _sensor_shard(sensor_id: str, shards: int = 16) -> int:
    return crc32(sensor_id.encode("utf-8")) % shards


def _date_range(start: date, end: date) -> list[date]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current = current + timedelta(days=1)
    return days


def _latest_first(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda row: row["reading_time"], reverse=True)


def _numeric_values(rows: list[dict[str, Any]], field: str) -> list[float]:
    return [float(row[field]) for row in rows if row.get(field) is not None]


def _summary_cache_key(sensor_id: str) -> str:
    return SUMMARY_CACHE_KEY_TEMPLATE.format(sensor_id=sensor_id)


async def _store_single_reading(
    payload: SensorReadingCreate,
    cassandra: CassandraStore,
) -> SensorReadingOut:
    reading_time = _as_utc(payload.reading_time or _utc_now())
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


async def _read_sensor_partition(
    cassandra: CassandraStore,
    sensor_id: str,
    bucket_day: date,
    limit: int,
    from_time: datetime | None = None,
) -> list[dict[str, Any]]:
    if from_time is None:
        rows = await cassandra.execute(
            """
            SELECT sensor_id, sensor_type, bucket_day, reading_time, reading_id,
                   voltage, current, power_factor, temperature, quality_flag
            FROM sensor_readings
            WHERE sensor_id = %s AND bucket_day = %s
            LIMIT %s
            """,
            (sensor_id, bucket_day, limit),
        )
    else:
        rows = await cassandra.execute(
            """
            SELECT sensor_id, sensor_type, bucket_day, reading_time, reading_id,
                   voltage, current, power_factor, temperature, quality_flag
            FROM sensor_readings
            WHERE sensor_id = %s AND bucket_day = %s AND reading_time >= %s
            LIMIT %s
            """,
            (sensor_id, bucket_day, from_time, limit),
        )
    return [normalize_cassandra_row(row) for row in rows]


async def _read_recent_sensor_rows(
    cassandra: CassandraStore,
    sensor_id: str,
    limit: int,
    from_time: datetime | None = None,
    day: date | None = None,
) -> list[dict[str, Any]]:
    if day is not None:
        rows = await _read_sensor_partition(cassandra, sensor_id, day, limit, from_time)
        return _latest_first(rows)[:limit]

    now = _utc_now()
    if from_time is not None:
        start = _as_utc(from_time)
        # Protect the API from accidentally scanning an unbounded Cassandra date range.
        if now.date() - start.date() > timedelta(days=31):
            start = now - timedelta(days=31)
        days = _date_range(start.date(), now.date())
    else:
        start = None
        # Query recent day partitions until enough rows are found.
        days = [now.date() - timedelta(days=offset) for offset in range(0, 8)]

    rows: list[dict[str, Any]] = []
    for bucket_day in days:
        partition_rows = await _read_sensor_partition(cassandra, sensor_id, bucket_day, limit, start)
        rows.extend(partition_rows)
        if start is None and len(rows) >= limit:
            break

    return _latest_first(rows)[:limit]


@router.get("/ping")
async def sensors_ping() -> dict[str, str]:
    return {"router": "sensors", "status": "ok"}


@router.post(
    "/readings",
    response_model=SensorReadingOut | list[SensorReadingOut],
    status_code=status.HTTP_201_CREATED,
    summary="Ingest one or a batch of sensor readings into Cassandra",
)
async def create_reading(
    payload: SensorReadingCreate | list[SensorReadingCreate],
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
) -> SensorReadingOut | list[SensorReadingOut]:
    if isinstance(payload, list):
        return [await _store_single_reading(item, cassandra) for item in payload]
    return await _store_single_reading(payload, cassandra)


@router.get(
    "/{sensor_id}/readings",
    response_model=list[SensorReadingOut],
    summary="Read the last N readings for one sensor, optionally from a timestamp or Cassandra day bucket",
)
async def list_sensor_readings(
    sensor_id: str,
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
    limit: Annotated[int, Query(ge=1, le=500, description="Maximum number of readings to return")] = 100,
    from_time: Annotated[datetime | None, Query(description="Optional lower timestamp bound, e.g. 2026-06-05T13:00:00Z")] = None,
    day: Annotated[date | None, Query(description="Optional Cassandra partition day, kept for efficient exact-day reads")] = None,
) -> list[dict[str, Any]]:
    return await _read_recent_sensor_rows(cassandra, sensor_id, limit, from_time, day)


@router.get(
    "/{sensor_id}/summary",
    response_model=SensorSummary,
    summary="Return cached latest reading plus one-hour stats for a sensor",
)
async def sensor_summary(
    sensor_id: str,
    cassandra: Annotated[CassandraStore, Depends(get_cassandra)],
    redis: Annotated[RedisStore, Depends(get_redis)],
) -> dict[str, Any]:
    client = redis.require_client()
    cache_key = _summary_cache_key(sensor_id)
    cached = await client.get(cache_key)
    if cached is not None:
        data = json.loads(cached)
        data["cached"] = True
        return data

    to_time = _utc_now()
    from_time = to_time - timedelta(hours=1)
    rows = await _read_recent_sensor_rows(cassandra, sensor_id, limit=500, from_time=from_time)
    latest = rows[0] if rows else None

    voltage_values = _numeric_values(rows, "voltage")
    current_values = _numeric_values(rows, "current")
    stats = SensorStats(
        count=len(rows),
        from_time=from_time,
        to_time=to_time,
        voltage_avg=mean(voltage_values) if voltage_values else None,
        voltage_min=min(voltage_values) if voltage_values else None,
        voltage_max=max(voltage_values) if voltage_values else None,
        current_avg=mean(current_values) if current_values else None,
        current_min=min(current_values) if current_values else None,
        current_max=max(current_values) if current_values else None,
    )
    summary = SensorSummary(
        sensor_id=sensor_id,
        latest_reading=latest,
        one_hour_stats=stats,
        cached=False,
        ttl_seconds=SUMMARY_CACHE_TTL_SECONDS,
    )
    data = summary.model_dump(mode="json")
    await client.set(cache_key, json.dumps(data), ex=SUMMARY_CACHE_TTL_SECONDS)
    return data


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
) -> list[dict[str, Any]]:
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
) -> list[dict[str, Any]]:
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
