#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import math
import os
import random
import sys
import time
import uuid
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from cassandra import ConsistencyLevel
from cassandra.cluster import Cluster, NoHostAvailable, Session
from cassandra.query import BoundStatement, PreparedStatement


CONSISTENCY_LEVELS: dict[str, int] = {
    "ONE": ConsistencyLevel.ONE,
    "LOCAL_QUORUM": ConsistencyLevel.LOCAL_QUORUM,
    "ALL": ConsistencyLevel.ALL,
}

INSERT_SENSOR_READING_CQL = """
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

DELETE_BENCH_PARTITION_CQL = """
DELETE FROM sensor_readings
WHERE sensor_id = ? AND bucket_day = ?
"""


@dataclass(frozen=True)
class BenchResult:
    consistency: str
    elapsed_seconds: float
    attempted: int
    successful: int
    errors: int
    events_per_second: float
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    error_types: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Cassandra sensor_readings writes by consistency level."
    )
    parser.add_argument(
        "--host",
        default=os.getenv("CASSANDRA_HOST", "localhost"),
        help="Cassandra host. Use localhost from host machine, or timeseries-db from Docker network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("CASSANDRA_PORT", "9042")),
        help="Cassandra native transport port.",
    )
    parser.add_argument(
        "--keyspace",
        default=os.getenv("CASSANDRA_KEYSPACE", "gridsense"),
        help="Cassandra keyspace containing sensor_readings.",
    )
    parser.add_argument(
        "--levels",
        default="ONE,LOCAL_QUORUM,ALL",
        help="Comma-separated consistency levels to test.",
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=30.0,
        help="Measured duration per consistency level.",
    )
    parser.add_argument(
        "--warmup-seconds",
        type=float,
        default=3.0,
        help="Warmup duration per consistency level; warmup writes are not included in results.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=64,
        help="Maximum in-flight write operations.",
    )
    parser.add_argument(
        "--sensor-count",
        type=int,
        default=100,
        help="Number of synthetic benchmark sensor IDs to spread writes across.",
    )
    parser.add_argument(
        "--sensor-prefix",
        default="BENCH_SENSOR",
        help="Prefix for synthetic sensor IDs written by this benchmark.",
    )
    parser.add_argument(
        "--csv",
        default="cassandra_write_consistency_results.csv",
        help=(
            "Path to write benchmark results as CSV. "
            "Parent directories are created automatically. "
            "Default: cassandra_write_consistency_results.csv"
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete benchmark sensor partitions at the end of the run.",
    )
    parser.add_argument(
        "--connect-timeout-seconds",
        type=float,
        default=120.0,
        help="How long to retry the Cassandra connection before failing.",
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=10.0,
        help="Per-query timeout passed to the Cassandra driver.",
    )
    return parser.parse_args()


def normalize_levels(levels_arg: str) -> list[str]:
    levels = [item.strip().upper() for item in levels_arg.split(",") if item.strip()]
    invalid = [level for level in levels if level not in CONSISTENCY_LEVELS]
    if invalid:
        valid = ", ".join(CONSISTENCY_LEVELS)
        raise SystemExit(f"Invalid consistency level(s): {invalid}. Valid values: {valid}")
    return levels


def connect_with_retry(args: argparse.Namespace) -> tuple[Cluster, Session]:
    deadline = time.monotonic() + args.connect_timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        cluster: Cluster | None = None
        try:
            cluster = Cluster([args.host], port=args.port)
            session = cluster.connect(args.keyspace)
            session.default_timeout = args.request_timeout_seconds
            return cluster, session
        except (NoHostAvailable, Exception) as exc:
            last_error = exc
            if cluster is not None:
                try:
                    cluster.shutdown()
                except Exception:
                    pass
            time.sleep(2)

    raise RuntimeError(
        f"Could not connect to Cassandra at {args.host}:{args.port}, "
        f"keyspace={args.keyspace}: {last_error}"
    )


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((pct / 100.0) * len(ordered)) - 1))
    return ordered[index]


def format_float(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def make_sensor_reading_args(
    event_index: int,
    *,
    base_time: datetime,
    sensor_count: int,
    sensor_prefix: str,
) -> tuple:
    sensor_number = (event_index % sensor_count) + 1
    sensor_id = f"{sensor_prefix}_{sensor_number:04d}"
    reading_time = base_time + timedelta(microseconds=event_index)

    voltage = 230.0 + random.random() * 5.0
    current = 15.0 + random.random() * 8.0
    power_factor = 0.90 + random.random() * 0.09
    temperature = 25.0 + random.random() * 10.0
    quality_flag = 0

    return (
        sensor_id,
        "benchmark_sensor",
        reading_time.date(),
        reading_time,
        uuid.uuid1(),
        voltage,
        current,
        power_factor,
        temperature,
        quality_flag,
    )


def write_one(
    session: Session,
    prepared_insert: PreparedStatement,
    consistency_level: int,
    values: tuple,
) -> tuple[bool, float, str | None]:
    bound: BoundStatement = prepared_insert.bind(values)
    bound.consistency_level = consistency_level

    start = time.perf_counter()
    try:
        session.execute(bound)
        latency_ms = (time.perf_counter() - start) * 1000.0
        return True, latency_ms, None
    except Exception as exc:
        latency_ms = (time.perf_counter() - start) * 1000.0
        message = f"{type(exc).__name__}: {str(exc).splitlines()[0]}"
        return False, latency_ms, message[:300]


def run_load_phase(
    *,
    session: Session,
    prepared_insert: PreparedStatement,
    consistency_name: str,
    duration_seconds: float,
    concurrency: int,
    sensor_count: int,
    sensor_prefix: str,
    base_time: datetime,
    starting_event_index: int,
    record_stats: bool,
) -> tuple[int, int, list[float], Counter[str], float, int]:
    consistency_level = CONSISTENCY_LEVELS[consistency_name]
    submitted = 0
    successful = 0
    latencies_ms: list[float] = []
    errors: Counter[str] = Counter()
    futures = set()

    start = time.perf_counter()
    submit_until = start + duration_seconds
    next_event_index = starting_event_index

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        while time.perf_counter() < submit_until or futures:
            while len(futures) < concurrency and time.perf_counter() < submit_until:
                values = make_sensor_reading_args(
                    next_event_index,
                    base_time=base_time,
                    sensor_count=sensor_count,
                    sensor_prefix=sensor_prefix,
                )
                next_event_index += 1
                submitted += 1
                futures.add(
                    executor.submit(
                        write_one,
                        session,
                        prepared_insert,
                        consistency_level,
                        values,
                    )
                )

            if not futures:
                continue

            done, futures = wait(futures, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                ok, latency_ms, error_message = future.result()
                if ok:
                    successful += 1
                    if record_stats:
                        latencies_ms.append(latency_ms)
                else:
                    if record_stats:
                        errors[error_message or "unknown error"] += 1

    elapsed = time.perf_counter() - start
    return submitted, successful, latencies_ms, errors, elapsed, next_event_index


def benchmark_consistency_level(
    *,
    session: Session,
    prepared_insert: PreparedStatement,
    consistency_name: str,
    args: argparse.Namespace,
    base_time: datetime,
    starting_event_index: int,
) -> tuple[BenchResult, int]:
    if args.warmup_seconds > 0:
        _, _, _, _, _, starting_event_index = run_load_phase(
            session=session,
            prepared_insert=prepared_insert,
            consistency_name=consistency_name,
            duration_seconds=args.warmup_seconds,
            concurrency=args.concurrency,
            sensor_count=args.sensor_count,
            sensor_prefix=args.sensor_prefix,
            base_time=base_time,
            starting_event_index=starting_event_index,
            record_stats=False,
        )

    attempted, successful, latencies_ms, errors, elapsed, next_event_index = run_load_phase(
        session=session,
        prepared_insert=prepared_insert,
        consistency_name=consistency_name,
        duration_seconds=args.duration_seconds,
        concurrency=args.concurrency,
        sensor_count=args.sensor_count,
        sensor_prefix=args.sensor_prefix,
        base_time=base_time,
        starting_event_index=starting_event_index,
        record_stats=True,
    )

    events_per_second = successful / elapsed if elapsed > 0 else 0.0
    error_types = "; ".join(f"{count}x {message}" for message, count in errors.most_common())

    return (
        BenchResult(
            consistency=consistency_name,
            elapsed_seconds=elapsed,
            attempted=attempted,
            successful=successful,
            errors=sum(errors.values()),
            events_per_second=events_per_second,
            p50_ms=percentile(latencies_ms, 50),
            p95_ms=percentile(latencies_ms, 95),
            max_ms=max(latencies_ms) if latencies_ms else None,
            error_types=error_types or "none",
        ),
        next_event_index,
    )


def cleanup_benchmark_rows(
    session: Session,
    sensor_prefix: str,
    sensor_count: int,
    bucket_days: Iterable,
) -> None:
    prepared_delete = session.prepare(DELETE_BENCH_PARTITION_CQL)
    prepared_delete.consistency_level = ConsistencyLevel.ONE

    deleted = 0
    for sensor_number in range(1, sensor_count + 1):
        sensor_id = f"{sensor_prefix}_{sensor_number:04d}"
        for bucket_day in bucket_days:
            session.execute(prepared_delete, (sensor_id, bucket_day))
            deleted += 1

    print(f"Cleanup complete: issued {deleted} partition delete(s).")


def print_results(results: list[BenchResult]) -> None:
    headers = [
        "Consistency",
        "Events/s",
        "p50 ms",
        "p95 ms",
        "Max ms",
        "Attempted",
        "Successful",
        "Errors",
        "Elapsed s",
        "Error types",
    ]
    rows = [
        [
            result.consistency,
            format_float(result.events_per_second, 2),
            format_float(result.p50_ms, 3),
            format_float(result.p95_ms, 3),
            format_float(result.max_ms, 3),
            str(result.attempted),
            str(result.successful),
            str(result.errors),
            format_float(result.elapsed_seconds, 3),
            result.error_types,
        ]
        for result in results
    ]

    widths = [len(header) for header in headers]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))

    def line(parts: list[str]) -> str:
        return " | ".join(part.ljust(widths[idx]) for idx, part in enumerate(parts))

    print()
    print(line(headers))
    print("-+-".join("-" * width for width in widths))
    for row in rows:
        print(line(row))


def write_csv(path: str, results: list[BenchResult]) -> Path:
    csv_path = Path(path).expanduser()

    # If the user passes something like results/cassandra/results.csv,
    # create results/cassandra automatically before opening the CSV.
    if csv_path.parent and str(csv_path.parent) not in ("", "."):
        csv_path.parent.mkdir(parents=True, exist_ok=True)

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "consistency",
                "elapsed_seconds",
                "attempted",
                "successful",
                "errors",
                "events_per_second",
                "p50_ms",
                "p95_ms",
                "max_ms",
                "error_types",
            ],
        )
        writer.writeheader()
        for result in results:
            writer.writerow(
                {
                    "consistency": result.consistency,
                    "elapsed_seconds": f"{result.elapsed_seconds:.6f}",
                    "attempted": result.attempted,
                    "successful": result.successful,
                    "errors": result.errors,
                    "events_per_second": f"{result.events_per_second:.6f}",
                    "p50_ms": "" if result.p50_ms is None else f"{result.p50_ms:.6f}",
                    "p95_ms": "" if result.p95_ms is None else f"{result.p95_ms:.6f}",
                    "max_ms": "" if result.max_ms is None else f"{result.max_ms:.6f}",
                    "error_types": result.error_types,
                }
            )

    return csv_path


def main() -> int:
    args = parse_args()
    levels = normalize_levels(args.levels)

    if args.concurrency < 1:
        raise SystemExit("--concurrency must be >= 1")
    if args.sensor_count < 1:
        raise SystemExit("--sensor-count must be >= 1")
    if args.duration_seconds <= 0:
        raise SystemExit("--duration-seconds must be > 0")
    if args.warmup_seconds < 0:
        raise SystemExit("--warmup-seconds must be >= 0")

    print(
        f"Connecting to Cassandra at {args.host}:{args.port}, "
        f"keyspace={args.keyspace}"
    )
    cluster, session = connect_with_retry(args)

    try:
        prepared_insert = session.prepare(INSERT_SENSOR_READING_CQL)
        base_time = datetime.now(timezone.utc).replace(microsecond=0)
        next_event_index = 0
        results: list[BenchResult] = []

        print(
            f"Benchmark settings: levels={levels}, duration={args.duration_seconds}s, "
            f"warmup={args.warmup_seconds}s, concurrency={args.concurrency}, "
            f"sensor_count={args.sensor_count}"
        )

        for level in levels:
            print(f"\nRunning {level}...")
            result, next_event_index = benchmark_consistency_level(
                session=session,
                prepared_insert=prepared_insert,
                consistency_name=level,
                args=args,
                base_time=base_time,
                starting_event_index=next_event_index,
            )
            results.append(result)

            print(
                f"{level}: {result.events_per_second:.2f} events/s, "
                f"p50={format_float(result.p50_ms)} ms, "
                f"p95={format_float(result.p95_ms)} ms, "
                f"successful={result.successful}, "
                f"errors={result.errors}"
            )

            # Write after each level so you still keep partial results
            # if a later level fails or is interrupted.
            csv_path = write_csv(args.csv, results)
            print(f"Partial CSV updated: {csv_path}")

        print_results(results)

        csv_path = write_csv(args.csv, results)
        print(f"\nCSV written to: {csv_path}")

        if args.cleanup:
            cleanup_benchmark_rows(
                session,
                sensor_prefix=args.sensor_prefix,
                sensor_count=args.sensor_count,
                bucket_days={base_time.date()},
            )

        return 0

    finally:
        session.shutdown()
        cluster.shutdown()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
