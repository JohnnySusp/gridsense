import math
import os
import uuid
from zlib import crc32
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, List, Tuple

from cassandra.cluster import Cluster
from cassandra.concurrent import execute_concurrent_with_args


SENSOR_COUNT = 20
READINGS_PER_SENSOR = 2_500
SHARD_COUNT = 16
BASE_TIME = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
CHUNK_SIZE = 1_000
CONCURRENCY = 100


def deterministic_timeuuid(dt: datetime, sensor_idx: int, reading_idx: int) -> uuid.UUID:
    """
    Create a deterministic UUIDv1-compatible value for Cassandra TIMEUUID.

    This makes the seed idempotent: rerunning the script produces the same
    primary keys instead of creating duplicate logical readings.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    # UUIDv1 timestamp: 100ns intervals since 1582-10-15.
    uuid_epoch_offset = 0x01B21DD213814000
    timestamp_100ns = int(dt.timestamp() * 10_000_000) + uuid_epoch_offset

    time_low = timestamp_100ns & 0xFFFFFFFF
    time_mid = (timestamp_100ns >> 32) & 0xFFFF
    time_hi_version = ((timestamp_100ns >> 48) & 0x0FFF) | (1 << 12)

    clock_seq = ((sensor_idx * 251) + reading_idx) & 0x3FFF
    clock_seq_hi_variant = ((clock_seq >> 8) & 0x3F) | 0x80
    clock_seq_low = clock_seq & 0xFF

    node = (0x123400000000 + sensor_idx) & 0xFFFFFFFFFFFF

    return uuid.UUID(
        fields=(
            time_low,
            time_mid,
            time_hi_version,
            clock_seq_hi_variant,
            clock_seq_low,
            node,
        )
    )


def floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def sensor_shard(sensor_id: str) -> int:
    return crc32(sensor_id.encode("utf-8")) % SHARD_COUNT


def generate_readings() -> Iterator[Tuple[tuple, tuple]]:
    for sensor_idx in range(1, SENSOR_COUNT + 1):
        sensor_id = f"SENSOR_{sensor_idx:03d}"
        sensor_type = "smart_meter" if sensor_idx <= 10 else "field_sensor"

        for reading_idx in range(READINGS_PER_SENSOR):
            reading_time = BASE_TIME + timedelta(seconds=reading_idx)
            reading_id = deterministic_timeuuid(reading_time, sensor_idx, reading_idx)

            voltage = round(230.0 + 4.5 * math.sin(reading_idx / 60 + sensor_idx), 3)
            current = round(18.0 + 3.0 * math.sin(reading_idx / 45 + sensor_idx / 2), 3)
            power_factor = round(0.94 + 0.03 * math.sin(reading_idx / 90), 4)
            temperature = round(28.0 + 6.0 * math.sin(reading_idx / 300 + sensor_idx), 3)
            quality_flag = 1 if reading_idx % 997 == 0 else 0

            bucket_day = reading_time.date()
            bucket_start = floor_to_minute(reading_time)
            shard = sensor_shard(sensor_id)

            sensor_readings_args = (
                sensor_id,
                sensor_type,
                bucket_day,
                reading_time,
                reading_id,
                voltage,
                current,
                power_factor,
                temperature,
                quality_flag,
            )

            bucket_readings_args = (
                bucket_start,
                shard,
                sensor_id,
                sensor_type,
                reading_time,
                reading_id,
                voltage,
                current,
                power_factor,
                temperature,
                quality_flag,
            )

            yield sensor_readings_args, bucket_readings_args


def chunks(items: Iterable[Tuple[tuple, tuple]], size: int) -> Iterator[List[Tuple[tuple, tuple]]]:
    chunk: List[Tuple[tuple, tuple]] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def main() -> None:
    cassandra_host = os.getenv("CASSANDRA_HOST", "timeseries-db")
    cassandra_keyspace = os.getenv("CASSANDRA_KEYSPACE", "gridsense")

    print(f"Connecting to Cassandra at {cassandra_host}:9042, keyspace={cassandra_keyspace}")

    cluster = Cluster([cassandra_host], port=9042)
    session = cluster.connect(cassandra_keyspace)

    insert_sensor_reading = session.prepare(
        """
        INSERT INTO sensor_readings (
          sensor_id,
          sensor_type,
          bucket_day,
          reading_time,
          reading_id,
          voltage,
          current,
          power_factor,
          temperature,
          quality_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    )

    insert_bucket_reading = session.prepare(
        """
        INSERT INTO sensor_readings_by_bucket (
          bucket_start,
          shard,
          sensor_id,
          sensor_type,
          reading_time,
          reading_id,
          voltage,
          current,
          power_factor,
          temperature,
          quality_flag
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
    )

    total = 0

    for chunk in chunks(generate_readings(), CHUNK_SIZE):
        sensor_args = [pair[0] for pair in chunk]
        bucket_args = [pair[1] for pair in chunk]

        execute_concurrent_with_args(
            session,
            insert_sensor_reading,
            sensor_args,
            concurrency=CONCURRENCY,
            raise_on_first_error=True,
        )

        execute_concurrent_with_args(
            session,
            insert_bucket_reading,
            bucket_args,
            concurrency=CONCURRENCY,
            raise_on_first_error=True,
        )

        total += len(chunk)
        print(f"Seeded {total:,}/{SENSOR_COUNT * READINGS_PER_SENSOR:,} logical readings")

    session.shutdown()
    cluster.shutdown()

    print("Done.")
    print(f"Logical readings seeded: {total:,}")
    print(f"Cassandra table rows written: {total * 2:,}")


if __name__ == "__main__":
    main()