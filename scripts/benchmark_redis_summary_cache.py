#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


CACHE_KEY_TEMPLATE = "gridsense:sensors:{sensor_id}:summary"
DEFAULT_TTL_SECONDS = 30


@dataclass(frozen=True)
class RequestResult:
    latency_ms: float
    ok: bool
    cached: bool | None
    status_code: int | None
    error: str | None = None


def percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (pct / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    weight = rank - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def summary_url(api_url: str, sensor_id: str) -> str:
    return f"{api_url.rstrip('/')}/sensors/{quote(sensor_id, safe='')}/summary"


def request_summary(api_url: str, sensor_id: str, timeout: float) -> RequestResult:
    url = summary_url(api_url, sensor_id)
    request = Request(url, method="GET", headers={"Accept": "application/json"})

    start = time.perf_counter_ns()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
            status_code = response.status
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000

        cached: bool | None = None
        try:
            payload = json.loads(body.decode("utf-8"))
            cached_value = payload.get("cached")
            if isinstance(cached_value, bool):
                cached = cached_value
        except json.JSONDecodeError:
            pass

        return RequestResult(
            latency_ms=latency_ms,
            ok=200 <= status_code < 300,
            cached=cached,
            status_code=status_code,
        )
    except HTTPError as exc:
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000
        return RequestResult(
            latency_ms=latency_ms,
            ok=False,
            cached=None,
            status_code=exc.code,
            error=f"HTTP {exc.code}: {exc.reason}",
        )
    except URLError as exc:
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000
        return RequestResult(
            latency_ms=latency_ms,
            ok=False,
            cached=None,
            status_code=None,
            error=str(exc.reason),
        )
    except Exception as exc:  # noqa: BLE001 - benchmark should report all request failures.
        latency_ms = (time.perf_counter_ns() - start) / 1_000_000
        return RequestResult(
            latency_ms=latency_ms,
            ok=False,
            cached=None,
            status_code=None,
            error=repr(exc),
        )


def make_redis_client(redis_url: str | None) -> Any | None:
    if not redis_url:
        return None
    try:
        import redis  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "The redis Python package is required for --cold-mode delete-each. "
            "Install it with: pip install redis"
        ) from exc

    client = redis.Redis.from_url(redis_url, decode_responses=True)
    client.ping()
    return client


def delete_cache_key(redis_client: Any | None, sensor_id: str) -> None:
    if redis_client is None:
        return
    redis_client.delete(CACHE_KEY_TEMPLATE.format(sensor_id=sensor_id))


def run_batch(
    *,
    name: str,
    api_url: str,
    sensor_id: str,
    requests: int,
    timeout: float,
    redis_client: Any | None,
    delete_before_each_request: bool,
) -> list[RequestResult]:
    results: list[RequestResult] = []

    for index in range(1, requests + 1):
        if delete_before_each_request:
            delete_cache_key(redis_client, sensor_id)

        result = request_summary(api_url, sensor_id, timeout)
        results.append(result)

        if index % 100 == 0 or index == requests:
            print(f"{name}: completed {index:,}/{requests:,} requests")

    return results


def summarize(name: str, results: list[RequestResult]) -> dict[str, Any]:
    ok_results = [result for result in results if result.ok]
    latencies = [result.latency_ms for result in ok_results]
    cache_known = [result for result in ok_results if result.cached is not None]
    cache_hits = sum(1 for result in cache_known if result.cached is True)
    errors = [result for result in results if not result.ok]

    return {
        "batch": name,
        "requests": len(results),
        "ok": len(ok_results),
        "errors": len(errors),
        "cache_hit_rate_pct": (cache_hits / len(cache_known) * 100.0) if cache_known else None,
        "p50_ms": percentile(latencies, 50),
        "p95_ms": percentile(latencies, 95),
        "p99_ms": percentile(latencies, 99),
        "min_ms": min(latencies) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "first_error": errors[0].error if errors else None,
    }


def print_summary(rows: list[dict[str, Any]]) -> None:
    headers = [
        "batch",
        "requests",
        "ok",
        "errors",
        "cache_hit_rate_pct",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "min_ms",
        "max_ms",
        "first_error",
    ]

    print("\nResults")
    print("-" * 118)
    print(
        f"{'batch':<14} {'requests':>9} {'ok':>9} {'errors':>9} "
        f"{'hit_rate%':>10} {'p50 ms':>10} {'p95 ms':>10} {'p99 ms':>10} {'min ms':>10} {'max ms':>10}"
    )
    print("-" * 118)

    for row in rows:
        def fmt(value: Any) -> str:
            if value is None:
                return "n/a"
            if isinstance(value, float):
                return f"{value:.3f}"
            return str(value)

        print(
            f"{row['batch']:<14} {row['requests']:>9} {row['ok']:>9} {row['errors']:>9} "
            f"{fmt(row['cache_hit_rate_pct']):>10} {fmt(row['p50_ms']):>10} "
            f"{fmt(row['p95_ms']):>10} {fmt(row['p99_ms']):>10} "
            f"{fmt(row['min_ms']):>10} {fmt(row['max_ms']):>10}"
        )

    error_rows = [row for row in rows if row.get("first_error")]
    if error_rows:
        print("\nFirst observed errors:")
        for row in error_rows:
            print(f"  {row['batch']}: {row['first_error']}")


def write_csv(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark Redis cache effectiveness for /sensors/{sensor_id}/summary."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--sensor-id", default="SENSOR_001")
    parser.add_argument("--requests-per-batch", type=int, default=500)
    parser.add_argument("--ttl-seconds", type=int, default=DEFAULT_TTL_SECONDS)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument(
        "--cold-mode",
        choices=("delete-each", "ttl-expire"),
        default="delete-each",
        help=(
            "delete-each makes all cold requests cache misses by deleting the Redis key before "
            "each request. ttl-expire waits for the TTL once before the cold batch."
        ),
    )
    parser.add_argument(
        "--redis-url",
        default=None,
        help="Example: redis://localhost:6379/0. Required for --cold-mode delete-each.",
    )
    parser.add_argument("--csv", default=None, help="Optional path to write summary CSV.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    redis_client = make_redis_client(args.redis_url) if args.redis_url else None
    if args.cold_mode == "delete-each" and redis_client is None:
        print(
            "ERROR: --cold-mode delete-each requires --redis-url so the script can clear the cache key.\n"
            "Either expose Redis and pass --redis-url redis://localhost:6379/0, or use --cold-mode ttl-expire.",
            file=sys.stderr,
        )
        return 2

    print(f"Endpoint: {summary_url(args.api_url, args.sensor_id)}")
    print(f"Requests: {args.requests_per_batch:,} warm + {args.requests_per_batch:,} cold")
    print(f"Cold mode: {args.cold_mode}\n")

    # Start from a known state when Redis is available.
    delete_cache_key(redis_client, args.sensor_id)

    print("Pre-warming cache with one request...")
    prewarm = request_summary(args.api_url, args.sensor_id, args.timeout)
    if not prewarm.ok:
        print(f"ERROR: prewarm request failed: {prewarm.error}", file=sys.stderr)
        return 1

    warm_results = run_batch(
        name="warm_cache",
        api_url=args.api_url,
        sensor_id=args.sensor_id,
        requests=args.requests_per_batch,
        timeout=args.timeout,
        redis_client=redis_client,
        delete_before_each_request=False,
    )

    if args.cold_mode == "ttl-expire":
        wait_seconds = args.ttl_seconds + 1
        print(f"\nWaiting {wait_seconds} seconds for cache TTL to expire before cold batch...")
        time.sleep(wait_seconds)
        cold_delete_each = False
    else:
        print("\nRunning cold batch by deleting the cache key before every request...")
        cold_delete_each = True

    cold_results = run_batch(
        name="cold_cache",
        api_url=args.api_url,
        sensor_id=args.sensor_id,
        requests=args.requests_per_batch,
        timeout=args.timeout,
        redis_client=redis_client,
        delete_before_each_request=cold_delete_each,
    )

    rows = [summarize("warm_cache", warm_results), summarize("cold_cache", cold_results)]
    print_summary(rows)

    if args.csv:
        write_csv(args.csv, rows)
        print(f"\nWrote CSV: {args.csv}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
