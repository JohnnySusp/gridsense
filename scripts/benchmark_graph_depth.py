#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def percentile(values, p):
    if not values:
        return None

    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    k = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(k)
    upper = math.ceil(k)

    if lower == upper:
        return ordered[int(k)]

    lower_value = ordered[lower]
    upper_value = ordered[upper]
    weight = k - lower

    return lower_value + (upper_value - lower_value) * weight


def get_json(api_base, endpoint_template, node_id, depth, timeout):
    encoded_node_id = quote(node_id, safe="")
    path = endpoint_template.format(node_id=encoded_node_id)

    separator = "&" if "?" in path else "?"
    url = f"{api_base.rstrip('/')}{path}{separator}{urlencode({'max_depth': depth})}"

    started = time.perf_counter_ns()

    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            latency_ms = (time.perf_counter_ns() - started) / 1_000_000
            return latency_ms, response.status, json.loads(body)

    except HTTPError as error:
        latency_ms = (time.perf_counter_ns() - started) / 1_000_000
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {error.code} for {url}: {error_body}"
        ) from error

    except URLError as error:
        raise RuntimeError(f"Could not connect to API at {url}: {error}") from error


def find_candidate_node(neo4j_uri, neo4j_user, neo4j_password, min_downstream):
    try:
        from neo4j import GraphDatabase
    except ImportError as exc:
        raise RuntimeError(
            "The neo4j Python package is not installed. "
            "Either install it or pass --node-id manually."
        ) from exc

    query = """
    MATCH (n)
    WHERE n.node_id IS NOT NULL
    MATCH (n)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..8]->(d)
    WITH n, count(DISTINCT d) AS downstream_count
    WHERE downstream_count >= $min_downstream
    RETURN
        n.node_id AS node_id,
        labels(n)[0] AS node_type,
        downstream_count
    ORDER BY downstream_count DESC
    LIMIT 1
    """

    driver = GraphDatabase.driver(
        neo4j_uri,
        auth=(neo4j_user, neo4j_password),
    )

    try:
        with driver.session(database=os.getenv("NEO4J_DATABASE", "neo4j")) as session:
            record = session.run(
                query,
                min_downstream=min_downstream,
            ).single()

            if record is None:
                raise RuntimeError(
                    f"No node found with at least {min_downstream} downstream nodes."
                )

            return {
                "node_id": record["node_id"],
                "node_type": record["node_type"],
                "downstream_count": record["downstream_count"],
            }
    finally:
        driver.close()


def maybe_write_chart(summary_rows, output_dir):
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib is not installed, so the PNG chart was skipped.")
        print("The CSV files were still written.")
        return

    depths = [row["depth"] for row in summary_rows]
    medians = [row["median_ms"] for row in summary_rows]
    p95s = [row["p95_ms"] for row in summary_rows]

    plt.figure(figsize=(8, 5))
    plt.plot(depths, medians, marker="o", label="Median")
    plt.plot(depths, p95s, marker="o", label="P95")
    plt.axhline(200, linestyle="--", label="200 ms SLA")

    plt.title("Graph traversal depth vs latency")
    plt.xlabel("max_depth")
    plt.ylabel("Latency (ms)")
    plt.xticks(depths)
    plt.legend()
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()

    chart_path = output_dir / "graph_depth_latency.png"
    plt.savefig(chart_path, dpi=160)
    plt.close()

    print(f"Wrote chart: {chart_path}")

def extract_total_affected(payload):
    if isinstance(payload, list):
        return len(payload)

    if isinstance(payload, dict):
        for key in ("total_affected", "affected_count", "count"):
            value = payload.get(key)
            if isinstance(value, int):
                return value

        for key in (
            "affected_nodes",
            "impacted_nodes",
            "downstream_nodes",
            "nodes",
            "results",
        ):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)

    return None


def format_ms(value):
    if value is None:
        return "-"
    return f"{value:.3f}"


def print_summary_board(summary_rows):
    headers = [
        "max_depth",
        "affected nodes",
        "median latency (ms)",
        "P95 latency (ms)",
    ]

    rows = [
        [
            str(row["depth"]),
            f"{row['total_affected']:,}",
            format_ms(row["median_ms"]),
            format_ms(row["p95_ms"]),
        ]
        for row in summary_rows
    ]

    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows))
        for column in range(len(headers))
    ]

    def border(left, middle, right):
        return (
            left
            + middle.join("─" * (width + 2) for width in widths)
            + right
        )

    def render_row(values):
        return (
            "│ "
            + " │ ".join(
                str(value).rjust(widths[index])
                for index, value in enumerate(values)
            )
            + " │"
        )

    print("\nGraph depth latency summary\n")
    print(border("┌", "┬", "┐"))
    print(render_row(headers))
    print(border("├", "┼", "┤"))

    for row in rows:
        print(render_row(row))

    print(border("└", "┴", "┘"))


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark /grid/fault-impact/{node_id} latency by max_depth."
    )

    parser.add_argument(
        "--api-base",
        default=os.getenv("API_BASE_URL", "http://localhost:8000"),
        help="Base URL for the FastAPI service.",
    )

    parser.add_argument(
        "--endpoint-template",
        default=os.getenv(
            "FAULT_IMPACT_ENDPOINT",
            "/grid/fault-impact/{node_id}",
        ),
        help="Endpoint path template. Use {node_id} where the encoded node id should go.",
    )

    parser.add_argument(
        "--node-id",
        default=None,
        help="Node id to test. If omitted, the script auto-selects one from Neo4j.",
    )

    parser.add_argument(
        "--iterations",
        type=int,
        default=30,
        help="Measured iterations per depth.",
    )

    parser.add_argument(
        "--warmup",
        type=int,
        default=5,
        help="Warm-up requests per depth, not included in results.",
    )

    parser.add_argument(
        "--min-downstream",
        type=int,
        default=20,
        help="Minimum downstream nodes required for auto-selected test node.",
    )

    parser.add_argument(
        "--max-depth-start",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--max-depth-end",
        type=int,
        default=8,
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
    )

    parser.add_argument(
        "--output-dir",
        default="results",
    )

    parser.add_argument(
        "--neo4j-uri",
        default=os.getenv("NEO4J_URI") or os.getenv("NEO4J_BOLT_URI"),
    )

    parser.add_argument(
        "--neo4j-user",
        default=os.getenv("NEO4J_USER", "neo4j"),
    )

    parser.add_argument(
        "--neo4j-password",
        default=os.getenv("NEO4J_PASSWORD"),
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    node_id = args.node_id
    selected_node = None

    if not node_id:
        if not args.neo4j_uri:
            raise SystemExit(
                "No --node-id was provided and NEO4J_URI is not set. "
                "Pass --node-id manually or set --neo4j-uri."
            )

        if not args.neo4j_password:
            raise SystemExit(
                "No --node-id was provided and NEO4J_PASSWORD is not set. "
                "Pass --node-id manually or set --neo4j-password."
            )

        selected_node = find_candidate_node(
            neo4j_uri=args.neo4j_uri,
            neo4j_user=args.neo4j_user,
            neo4j_password=args.neo4j_password,
            min_downstream=args.min_downstream,
        )
        node_id = selected_node["node_id"]

    print(f"Benchmarking node_id={node_id}")

    if selected_node:
        print(
            "Selected node: "
            f"type={selected_node['node_type']}, "
            f"downstream_count_depth_8={selected_node['downstream_count']}"
        )

    raw_rows = []
    summary_rows = []

    for depth in range(args.max_depth_start, args.max_depth_end + 1):
        print(f"\nDepth {depth}: warm-up x{args.warmup}")

        for _ in range(args.warmup):
            get_json(
                api_base=args.api_base,
                endpoint_template=args.endpoint_template,
                node_id=node_id,
                depth=depth,
                timeout=args.timeout,
            )

        print(f"Depth {depth}: measured iterations x{args.iterations}")

        latencies = []
        affected_counts = []

        for iteration in range(1, args.iterations + 1):
            latency_ms, status_code, payload = get_json(
                api_base=args.api_base,
                endpoint_template=args.endpoint_template,
                node_id=node_id,
                depth=depth,
                timeout=args.timeout,
            )

            total_affected = extract_total_affected(payload)
            latencies.append(latency_ms)
            affected_counts.append(total_affected)

            raw_rows.append(
                {
                    "depth": depth,
                    "iteration": iteration,
                    "latency_ms": round(latency_ms, 3),
                    "status_code": status_code,
                    "total_affected": total_affected,
                }
            )

        summary = {
            "depth": depth,
            "iterations": args.iterations,
            "total_affected": max(
                count for count in affected_counts if count is not None
            ),
            "median_ms": round(statistics.median(latencies), 3),
            "p95_ms": round(percentile(latencies, 95), 3),
            "mean_ms": round(statistics.mean(latencies), 3),
            "min_ms": round(min(latencies), 3),
            "max_ms": round(max(latencies), 3),
        }

        summary_rows.append(summary)

        print(
            f"Depth {depth}: "
            f"affected={summary['total_affected']}, "
            f"median={summary['median_ms']} ms, "
            f"p95={summary['p95_ms']} ms"
        )

    raw_path = output_dir / "graph_depth_latency_raw.csv"
    summary_path = output_dir / "graph_depth_latency_summary.csv"

    with raw_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "depth",
                "iteration",
                "latency_ms",
                "status_code",
                "total_affected",
            ],
        )
        writer.writeheader()
        writer.writerows(raw_rows)

    with summary_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "depth",
                "iterations",
                "total_affected",
                "median_ms",
                "p95_ms",
                "mean_ms",
                "min_ms",
                "max_ms",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nWrote raw results: {raw_path}")
    print(f"Wrote summary results: {summary_path}")

    maybe_write_chart(summary_rows, output_dir)

    print_summary_board(summary_rows)


if __name__ == "__main__":
    main()
