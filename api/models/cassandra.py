from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class SensorReadingCreate(BaseModel):
    sensor_id: str = Field(..., min_length=1, examples=["SM_00001"])
    sensor_type: str = Field(..., min_length=1, examples=["smart_meter"])
    reading_time: datetime | None = None
    voltage: float | None = None
    current: float | None = None
    power_factor: float | None = Field(default=None, ge=0, le=1)
    temperature: float | None = None
    quality_flag: int = Field(default=0, ge=0, le=127)


class SensorReadingOut(SensorReadingCreate):
    bucket_day: date
    reading_id: UUID


class SensorStats(BaseModel):
    count: int
    from_time: datetime
    to_time: datetime
    voltage_avg: float | None = None
    voltage_min: float | None = None
    voltage_max: float | None = None
    current_avg: float | None = None
    current_min: float | None = None
    current_max: float | None = None


class SensorSummary(BaseModel):
    sensor_id: str
    latest_reading: SensorReadingOut | None = None
    one_hour_stats: SensorStats
    cached: bool = False
    ttl_seconds: int = 30


class RelayEventCreate(BaseModel):
    feeder_id: str = Field(..., min_length=1, examples=["F_001"])
    relay_id: str = Field(..., min_length=1, examples=["RY_001"])
    event_type: str = Field(..., examples=["trip"])
    fault_type: str | None = Field(default=None, examples=["overcurrent"])
    current_ka: float | None = None


class RelayEventOut(RelayEventCreate):
    event_time: UUID


class TimeBucketReading(BaseModel):
    bucket_start: datetime
    shard: int
    sensor_id: str
    sensor_type: str
    reading_time: datetime
    reading_id: UUID
    voltage: float | None = None
    current: float | None = None
    power_factor: float | None = None
    temperature: float | None = None
    quality_flag: int


def normalize_cassandra_value(value: Any) -> Any:
    """Convert Cassandra driver values into JSON/Pydantic-friendly Python values."""
    if value is None:
        return None

    if isinstance(value, (datetime, date, UUID)):
        return value

    # Cassandra DATE may be returned as cassandra.util.Date, e.g. Date(20605).
    days_from_epoch = getattr(value, "days_from_epoch", None)
    if days_from_epoch is not None:
        return date(1970, 1, 1) + timedelta(days=int(days_from_epoch))

    # Fallback for other date-like objects.
    value_date = getattr(value, "date", None)
    if callable(value_date):
        return value_date()

    return value


def normalize_cassandra_row(row: Any) -> dict[str, Any]:
    """Return a JSON/Pydantic-friendly copy without mutating driver row objects."""
    if hasattr(row, "_asdict"):
        data = row._asdict()
    else:
        data = dict(row)

    return {key: normalize_cassandra_value(value) for key, value in data.items()}
