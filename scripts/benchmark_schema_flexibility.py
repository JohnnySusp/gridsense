#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import os
import statistics
import time
from pathlib import Path

from pymongo import MongoClient
import psycopg2
from psycopg2.extras import Json


def json_safe(value):
    return json.loads(json.dumps(value, default=str))


def get_postgres_dsn(args):
    if args.postgres_dsn:
        return args.postgres_dsn.replace("postgresql+asyncpg://", "postgresql://")

    env_dsn = os.getenv("POSTGRES_DSN")
    if env_dsn:
        return env_dsn.replace("postgresql+asyncpg://", "postgresql://")

    user = os.getenv("POSTGRES_USER", "gridsense")
    password = os.getenv("POSTGRES_PASSWORD", "gridsense")
    db = os.getenv("POSTGRES_DB", "gridsense")
    host = os.getenv("POSTGRES_HOST", "billing-db")
    port = os.getenv("POSTGRES_PORT", "5432")

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def normalize_equipment(doc):
    equipment_id = (
        doc.get("equipment_id")
        or doc.get("asset_id")
        or doc.get("meter_id")
        or doc.get("id")
        or str(doc.get("_id"))
    )

    equipment_type = (
        doc.get("type")
        or doc.get("equipment_type")
        or doc.get("category")
        or "Unknown"
    )

    if isinstance(doc.get("metadata"), dict):
        metadata = dict(doc["metadata"])
    else:
        excluded = {
            "_id",
            "id",
            "equipment_id",
            "asset_id",
            "meter_id",
            "type",
            "equipment_type",
            "category",
        }
        metadata = {key: value for key, value in doc.items() if key not in excluded}

    return {
        "equipment_id": str(equipment_id),
        "type": str(equipment_type),
        "metadata": json_safe(metadata),
    }


def load_source_records(mongo_db, source_collection, limit):
    docs = list(mongo_db[source_collection].find({}).limit(limit))

    if len(docs) < limit:
        raise RuntimeError(
            f"Expected at least {limit} records in MongoDB collection "
            f"'{source_collection}', but found {len(docs)}."
        )

    return [normalize_equipment(doc) for doc in docs]


def reset_mongo_benchmark_collection(mongo_db, benchmark_collection, records):
    collection = mongo_db[benchmark_collection]
    collection.delete_many({})

    if records:
        collection.insert_many(records)

    return collection


def reset_postgres_benchmark_table(conn, table_name, records):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {table_name} (
                equipment_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                metadata JSONB NOT NULL
            );
            """
        )

        cur.execute(f"TRUNCATE TABLE {table_name};")

        for record in records:
            cur.execute(
                f"""
                INSERT INTO {table_name} (equipment_id, type, metadata)
                VALUES (%s, %s, %s)
                ON CONFLICT (equipment_id)
                DO UPDATE SET
                    type = EXCLUDED.type,
                    metadata = EXCLUDED.metadata;
                """,
                (
                    record["equipment_id"],
                    record["type"],
                    Json(record["metadata"]),
                ),
            )

    conn.commit()


def measure(fn, runs, warmup):
    for _ in range(warmup):
        fn()

    timings = []
    last_result = None

    for _ in range(runs):
        started = time.perf_counter_ns()
        last_result = fn()
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
        timings.append(elapsed_ms)

    return timings, last_result


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark MongoDB schema flexibility vs PostgreSQL JSONB."
    )

    parser.add_argument(
        "--mongo-uri",
        default=os.getenv("MONGO_URI", "mongodb://catalog-db:27017"),
    )
    parser.add_argument(
        "--mongo-db",
        default=os.getenv("MONGO_DB") or os.getenv("MONGO_DB_NAME") or "gridsense",
    )
    parser.add_argument(
        "--source-collection",
        default="equipment",
        help="Existing MongoDB collection containing the seeded equipment records.",
    )
    parser.add_argument(
        "--mongo-benchmark-collection",
        default="equipment_c4_benchmark",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=None,
    )
    parser.add_argument(
        "--postgres-table",
        default="equipment_c4_benchmark",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=10,
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=3,
    )
    parser.add_argument(
        "--output-dir",
        default="results",
    )

    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    mongo_client = MongoClient(args.mongo_uri)
    mongo_db = mongo_client[args.mongo_db]

    pg_dsn = get_postgres_dsn(args)
    pg_conn = psycopg2.connect(pg_dsn)
    pg_conn.autocommit = False

    print(f"Reading {args.limit} records from MongoDB collection '{args.source_collection}'...")
    records = load_source_records(
        mongo_db=mongo_db,
        source_collection=args.source_collection,
        limit=args.limit,
    )

    print(f"Writing normalized benchmark records to MongoDB collection '{args.mongo_benchmark_collection}'...")
    mongo_collection = reset_mongo_benchmark_collection(
        mongo_db=mongo_db,
        benchmark_collection=args.mongo_benchmark_collection,
        records=records,
    )

    print(f"Writing normalized benchmark records to PostgreSQL table '{args.postgres_table}'...")
    reset_postgres_benchmark_table(
        conn=pg_conn,
        table_name=args.postgres_table,
        records=records,
    )

    queries = [
        {
            "name": "firmware_version starts with 3.",
            "mongo": lambda: list(
                mongo_collection.find(
                    {"metadata.firmware_version": {"$regex": r"^3\."}},
                    {"_id": 0},
                )
            ),
            "postgres": lambda: postgres_fetch(
                pg_conn,
                f"""
                SELECT equipment_id, type, metadata
                FROM {args.postgres_table}
                WHERE metadata->>'firmware_version' LIKE '3.%';
                """,
            ),
        },
        {
            "name": "smart_meter rated_voltage > 230",
            "mongo": lambda: list(
                mongo_collection.find(
                    {
                        "type": "smart_meter",
                        "metadata.rated_voltage": {"$gt": 230},
                    },
                    {"_id": 0},
                )
            ),
            "postgres": lambda: postgres_fetch(
                pg_conn,
                f"""
                SELECT equipment_id, type, metadata
                FROM {args.postgres_table}
                WHERE type = 'smart_meter'
                  AND metadata ? 'rated_voltage'
                  AND (metadata->>'rated_voltage') ~ '^[0-9]+(\\.[0-9]+)?$'
                  AND (metadata->>'rated_voltage')::numeric > 230;
                """,
            ),
        },
        {
            "name": "count grouped by type",
            "mongo": lambda: list(
                mongo_collection.aggregate(
                    [
                        {"$group": {"_id": "$type", "count": {"$sum": 1}}},
                        {"$sort": {"_id": 1}},
                    ]
                )
            ),
            "postgres": lambda: postgres_fetch(
                pg_conn,
                f"""
                SELECT type, COUNT(*) AS count
                FROM {args.postgres_table}
                GROUP BY type
                ORDER BY type;
                """,
            ),
        },
    ]

    raw_rows = []
    summary_rows = []

    for query in queries:
        print(f"\nRunning query: {query['name']}")

        mongo_timings, mongo_result = measure(
            query["mongo"],
            runs=args.runs,
            warmup=args.warmup,
        )

        pg_timings, pg_result = measure(
            query["postgres"],
            runs=args.runs,
            warmup=args.warmup,
        )

        for run_index, latency_ms in enumerate(mongo_timings, start=1):
            raw_rows.append(
                {
                    "query": query["name"],
                    "database": "MongoDB",
                    "run": run_index,
                    "latency_ms": round(latency_ms, 4),
                    "result_count": len(mongo_result),
                }
            )

        for run_index, latency_ms in enumerate(pg_timings, start=1):
            raw_rows.append(
                {
                    "query": query["name"],
                    "database": "PostgreSQL JSONB",
                    "run": run_index,
                    "latency_ms": round(latency_ms, 4),
                    "result_count": len(pg_result),
                }
            )

        summary_rows.append(
            {
                "query": query["name"],
                "mongo_mean_ms": round(statistics.mean(mongo_timings), 4),
                "postgres_jsonb_mean_ms": round(statistics.mean(pg_timings), 4),
                "mongo_result_count": len(mongo_result),
                "postgres_result_count": len(pg_result),
            }
        )

        print(
            f"MongoDB mean={statistics.mean(mongo_timings):.4f} ms, "
            f"PostgreSQL JSONB mean={statistics.mean(pg_timings):.4f} ms"
        )

    raw_path = output_dir / "schema_flexibility_raw.csv"
    summary_path = output_dir / "schema_flexibility_summary.csv"

    with raw_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "query",
                "database",
                "run",
                "latency_ms",
                "result_count",
            ],
        )
        writer.writeheader()
        writer.writerows(raw_rows)

    with summary_path.open("w", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "query",
                "mongo_mean_ms",
                "postgres_jsonb_mean_ms",
                "mongo_result_count",
                "postgres_result_count",
            ],
        )
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"\nWrote raw results: {raw_path}")
    print(f"Wrote summary results: {summary_path}")

    print("\nMarkdown summary table:\n")
    print("| Query | MongoDB mean (ms) | PostgreSQL JSONB mean (ms) | MongoDB rows | PostgreSQL rows |")
    print("|---|---:|---:|---:|---:|")
    for row in summary_rows:
        print(
            f"| {row['query']} | "
            f"{row['mongo_mean_ms']} | "
            f"{row['postgres_jsonb_mean_ms']} | "
            f"{row['mongo_result_count']} | "
            f"{row['postgres_result_count']} |"
        )

    pg_conn.close()
    mongo_client.close()


def postgres_fetch(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


if __name__ == "__main__":
    main()
