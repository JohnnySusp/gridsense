# GridSense

## Description

GridSense is a Docker-based prototype for a smart power grid analytics and fault-management platform. The system models a regional electricity distribution network where engineers need to inspect sensor readings, equipment records, billing data, fault alerts, and grid topology through a single REST API.

The project follows a **polyglot persistence** design. Instead of forcing all data into one database, each storage technology is used for the workload that best matches its data model and operational strengths:

| Compose service | Technology       | Role                                                                    |
| --------------- | ---------------- | ----------------------------------------------------------------------- |
| `api`           | FastAPI / Python | REST gateway and application logic                                      |
| `timeseries-db` | Apache Cassandra | Sensor readings and relay event logs                                    |
| `graph-db`      | Neo4j            | Grid topology, upstream paths, and fault-impact traversal               |
| `catalog-db`    | MongoDB          | Equipment catalogue with specifications for the assets of the topology  |
| `billing-db`    | PostgreSQL       | Consumer accounts, invoices, and tariff rules requiring ACID guarantees |
| `cache`         | Redis            | Short-lived dashboard/cache data and alert support                      |

The main design decision of GridSense is that it should not be implemented as a single-database system. 

Thus, the workload combines several access patterns that pull in different directions:
* Sensor readings are write-heavy time-series data. Cassandra is used because the schema can be partitioned by sensor and time bucket, matching high-ingestion workloads and predictable time-window reads.
* Grid topology is connected data. Neo4j is used because fault-impact analysis requires traversing relationships between supply points, substations, transformers, and smart meters.
* Equipment metadata is heterogeneous. MongoDB is used to store information related to specifications, and because smart meters, transformers, substations and grid supply points may have different fields depending on model, manufacturer, and firmware generation.
* Billing records require strong consistency. PostgreSQL is used for accounts, invoices, tariff rules, and balances because incorrect billing has regulatory and financial consequences.
* Dashboard and alert data can be short-lived and frequently read. Redis is used for fast cache-style access and alert support.

This implementation demonstrates the integration of these stores behind one API. It includes Docker Compose orchestration, database initialization scripts, deterministic seed data, REST endpoints for each storage role, and Prometheus-compatible metrics exposed at `/metrics`.

This is a local prototype rather than a production deployment. The Docker Compose setup demonstrates schema design, service integration, startup reproducibility, and API behaviour. It does not provide production-grade clustering, multi-datacenter failover, or the full throughput of the real smart-grid scenario.


## Fresh Clone Test

The following commands are intended for a fresh clone of the repository. 

They start the system from empty Docker volumes, seed all databases, verify expected record counts, exercise representative REST API endpoints, and check that Prometheus metrics show successful requests with no server-side `5xx` errors.


### 1. Requirements

The test machine should have the following tools installed:

* Git
* Docker
* Docker Compose
* curl
* Python 3

They can be checked with:

```bash
git --version
docker --version
docker compose version
docker info >/dev/null 2>&1 && echo "Docker daemon running" || echo "Docker daemon not running"
curl --version
python3 --version
```

### 2. Clone the repository

```bash
git clone https://github.com/JohnnySusp/gridsense.git
cd gridsense
```

### 3. Create the local environment file

The real `.env` file is intentionally not committed. It can be created from `.env.example`:

```bash
cp .env.example .env
```

Optionally, you can confirm that `.env` exists but is not tracked by Git:

```bash
ls -la .env .env.example
git ls-files | grep -E '(^|/)\.env$'
```

Expected result: the `git ls-files` command prints nothing.

### 4. Validate source files and Docker Compose configuration

```bash
python3 -m py_compile api/main.py api/config.py api/db/*.py api/models/*.py api/routers/*.py scripts/*.py

docker compose config >/tmp/gridsense-compose-check.yml && echo "Compose config parsed successfully"
```

### 5. Start from a clean Docker state

This removes existing GridSense containers, networks, and database volumes, then rebuilds and starts the full system.

```bash
docker compose down -v --remove-orphans
docker compose up --build -d
```

Check container state:

```bash
docker compose ps
```

Expected result:

* Cassandra, MongoDB, Neo4j, PostgreSQL, and Redis are healthy.
* The API container is running or healthy.
* `cassandra-init` has exited successfully.

Optionally, you can check the API startup logs:

```bash
docker compose logs --tail=120 api
```

Expected log lines include:

```text
Waiting for database ports...
timeseries-db:9042 is reachable
graph-db:7687 is reachable
catalog-db:27017 is reachable
billing-db:5432 is reachable
cache:6379 is reachable
Application startup complete.
```

### 6. Check API health before seeding

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expected result:

```json
{
    "status": "ok",
    "service": "gridsense-api"
}
```

### 7. Seed all databases

Run the seed orchestrator:

```bash
docker compose exec api python scripts/seed.py
```

The command above can be run a second time to confirm idempotence.

The second run should not increase the logical dataset size.

### 8. Load environment variables for direct database checks

```bash
set -a
source .env
set +a
```

### 9. Verify Neo4j graph data

```bash
docker compose exec graph-db cypher-shell \
  -u "$NEO4J_USER" \
  -p "$NEO4J_PASSWORD" \
  "
  MATCH (g:GridSupplyPoint) WITH count(g) AS grid_supply_points
  MATCH (s:Substation) WITH grid_supply_points, count(s) AS substations
  MATCH (t:Transformer) WITH grid_supply_points, substations, count(t) AS transformers
  MATCH (m:SmartMeter) WITH grid_supply_points, substations, transformers, count(m) AS smart_meters
  MATCH ()-[r]->()
  RETURN grid_supply_points, substations, transformers, smart_meters, count(r) AS relationships;
  "
```

Expected result:

```text
grid_supply_points = 1
substations = 10
transformers = 40
smart_meters = 200
relationships = 250
```

### 10. Verify Cassandra time-series data

Show the Cassandra tables:

```bash
docker compose exec timeseries-db cqlsh -e "USE gridsense; DESCRIBE TABLES;"
```

Expected tables include:

```text
relay_events
sensor_readings
sensor_readings_by_bucket
```

Check seeded row counts:

```bash
docker compose exec timeseries-db cqlsh -e \
  "USE gridsense; SELECT count(*) FROM sensor_readings;"

docker compose exec timeseries-db cqlsh -e \
  "USE gridsense; SELECT count(*) FROM sensor_readings_by_bucket;"

docker compose exec timeseries-db cqlsh -e \
  "USE gridsense; SELECT count(*) FROM sensor_readings
   WHERE sensor_id='SENSOR_001' AND bucket_day='2026-06-01';"
```

Expected result:

```text
sensor_readings = 50000
sensor_readings_by_bucket = 50000
SENSOR_001 on 2026-06-01 = 2500
```

A Cassandra warning about aggregation without a partition key is acceptable for this local verification step. This is not intended as a production query pattern.

### 11. Verify MongoDB equipment catalogue data

```bash
docker compose exec catalog-db mongosh \
  -u "$MONGO_INITDB_ROOT_USERNAME" \
  -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin \
  --quiet \
  --eval '
    db = db.getSiblingDB("gridsense");
    printjson({
      total: db.equipment.countDocuments(),
      types: db.equipment.aggregate([
        {$group: {_id: "$equipment_type", count: {$sum: 1}}},
        {$sort: {_id: 1}}
      ]).toArray(),
      smart_meter_example: db.equipment.findOne(
        {asset_id: "SM_001"},
        {_id: 0, asset_id: 1, equipment_type: 1, manufacturer: 1,
         firmware_version: 1, rated_voltage: 1,
         non_standard_telemetry_fields: 1}
      )
    });
  '
```

Expected result:

```text
total = 251
grid_supply_point = 1
smart_meter = 200
substation = 10
transformer = 40
```

The MongoDB catalogue mirrors the seeded Neo4j equipment assets for substations, transformers, and smart meters. For example, the Neo4j node with `node_id = "SM_001"` corresponds to the MongoDB equipment document with `asset_id = "SM_001"`. The `SM_001` example should include manufacturer/model data and flexible smart-meter metadata such as firmware, rated voltage, communication details, and non-standard telemetry fields.

### 12. Verify PostgreSQL billing data

```bash
docker compose exec billing-db sh -lc '
PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "
SELECT
  (SELECT count(*) FROM accounts) AS accounts,
  (SELECT count(*) FROM invoices) AS invoices;
"
'
```

Expected result:

```text
accounts = 100
invoices = 100
```

### 13. Verify Redis availability

```bash
docker compose exec cache redis-cli ping
```

Expected result:

```text
PONG
```

### 14. Test API metrics

```bash
curl -s http://localhost:8000/metrics | head -30
```

### 15. Test MongoDB-backed equipment endpoints

Test one document for each equipment/topology type:

```bash
curl -s http://localhost:8000/equipment/GSP_001 | python3 -m json.tool
```

Expected result: one grid-supply-point catalogue document with `asset_id` equal to `GSP_001`. This corresponds to the Neo4j `GridSupplyPoint` node with `node_id` equal to `GSP_001`.

```bash
curl -s http://localhost:8000/equipment/SS_001 | python3 -m json.tool
```

Expected result: one substation catalogue document with `asset_id` equal to `SS_001`. This corresponds to the Neo4j `Substation` node with `node_id` equal to `SS_001`.

```bash
curl -s http://localhost:8000/equipment/TX_001 | python3 -m json.tool
```

Expected result: one transformer equipment document with `asset_id` equal to `TX_001`. This corresponds to the Neo4j `Transformer` node with `node_id` equal to `TX_001`.

```bash
curl -s http://localhost:8000/equipment/SM_001 | python3 -m json.tool
```

Expected result: one smart-meter equipment document with `asset_id` equal to `SM_001`. This corresponds to the Neo4j `SmartMeter` node with `node_id` equal to `SM_001`.

### 16. Test Cassandra-backed sensor endpoints

The seeded sensor data is for `2026-06-01`, so the day parameter is included in the sensor query.

```bash
curl -s "http://localhost:8000/sensors/SENSOR_001/readings?day=2026-06-01&limit=5" | python3 -m json.tool
```

Expected result: five readings for `SENSOR_001`, with `bucket_day` equal to `2026-06-01`.

Test the network-wide recent readings endpoint:

```bash
curl -s "http://localhost:8000/sensors/readings/network-recent?from_time=2026-06-01T00:00:00Z&to_time=2026-06-01T00:01:00Z&limit=20" | python3 -m json.tool
```

Expected result: up to 20 recent readings from the bucketed sensor table, gathered by querying all configured bucket shards for the selected time window and merging the results in the API layer.

Optionally, to test the bucketed single-shard query table:

```bash
curl -s "http://localhost:8000/sensors/readings/by-bucket?bucket_start=2026-06-01T00:00:00Z&shard=0&limit=5" | python3 -m json.tool
```

### 17. Test Neo4j-backed graph endpoints

```bash
curl -s http://localhost:8000/grid/fault-impact/SS_001 | python3 -m json.tool
curl -s http://localhost:8000/grid/restore-paths/SS_001 | python3 -m json.tool
curl -s http://localhost:8000/grid/nodes/SS_001 | python3 -m json.tool
curl -s http://localhost:8000/grid/meters/SM_001/upstream | python3 -m json.tool
```

Expected result:

* `fault-impact/SS_001` returns downstream affected nodes.
* `restore-paths/SS_001` may return an empty list if no alternative path exists.
* `nodes/SS_001` returns the substation node.
* `meters/SM_001/upstream` returns a path from the grid supply point to the smart meter.

The graph endpoints return topology identifiers. When a returned node represents an equipment asset such as a substation, transformer, or smart meter, the same value can be used as `/equipment/{asset_id}` to retrieve its full MongoDB catalogue document.

### 18. Test PostgreSQL-backed billing endpoints

```bash
curl -s http://localhost:8000/billing/account/PREM_0001 | python3 -m json.tool
curl -s http://localhost:8000/billing/accounts/PREM_0001/invoices | python3 -m json.tool
```

Expected result:

* The first command returns the account for `PREM_0001`.
* The second command returns at least one invoice for `PREM_0001`.

### 19. Test Redis-backed alert endpoints

```bash
curl -s http://localhost:8000/alerts/active | python3 -m json.tool
curl -s http://localhost:8000/alerts/recent | python3 -m json.tool
```

Expected result: JSON arrays. If they are empty, it means that no alerts have been published.

### 20. Check Prometheus metrics after endpoint traffic

```bash
curl -s http://localhost:8000/metrics | grep -E "http_requests_total|http_request_duration_seconds" | head -160
```

Confirm selected endpoints have successful `2xx` metrics:

```bash
curl -s http://localhost:8000/metrics | grep 'equipment/{asset_id}'
curl -s http://localhost:8000/metrics | grep 'sensors/{sensor_id}/readings'
curl -s http://localhost:8000/metrics | grep 'sensors/readings/by-bucket'
curl -s http://localhost:8000/metrics | grep 'grid/fault-impact'
curl -s http://localhost:8000/metrics | grep 'grid/meters/{meter_id}/upstream'
curl -s http://localhost:8000/metrics | grep 'billing/account'
curl -s http://localhost:8000/metrics | grep 'alerts/active'
```

Confirm that no server-side API errors were recorded:

```bash
curl -s http://localhost:8000/metrics | grep 'status="5xx"' || echo "No 5xx recorded"
```

Expected result:

```text
No 5xx recorded
```

## Benchmark Tests

The following benchmarks measure Cassandra write consistency performance, Neo4j graph traversal latency, Redis cache effectiveness, and MongoDB versus PostgreSQL JSONB schema flexibility.

The repository already includes a `results` directory and benchmark output files. Before generating new benchmark results, empty the `results` folder:

```bash
rm -f results/*
```

Optionally, to check that the benchmark scripts compile:

```bash
docker compose exec api python -m py_compile scripts/*.py
```

### 1. Cassandra write consistency benchmark

This benchmark measures Cassandra write throughput and latency under different consistency levels.

```bash
docker compose exec api python scripts/benchmark_cassandra_write_consistency.py \
  --host timeseries-db \
  --keyspace gridsense \
  --levels ONE,LOCAL_QUORUM,ALL \
  --duration-seconds 30 \
  --warmup-seconds 3 \
  --concurrency 64 \
  --sensor-count 100 \
  --csv results/cassandra_write_consistency.csv \
  --cleanup
```

### 2. Neo4j graph traversal depth benchmark

This benchmark measures the latency of the fault-impact traversal endpoint while varying `max_depth` from 1 to 8.

```bash
docker compose exec api python scripts/benchmark_graph_depth.py \
  --iterations 30 \
  --warmup 5 \
  --output-dir results
```

### 3. Redis summary cache benchmark

This benchmark compares warm-cache and cold-cache latency for the sensor summary endpoint.

```bash
docker compose exec api python scripts/benchmark_redis_summary_cache.py \
  --api-url http://localhost:8000 \
  --sensor-id SENSOR_001 \
  --requests-per-batch 500 \
  --cold-mode delete-each \
  --redis-url redis://cache:6379/0 \
  --csv results/redis_summary_cache.csv
```

### 4. MongoDB vs PostgreSQL JSONB schema flexibility benchmark

This benchmark inserts the same 30 equipment records into MongoDB and PostgreSQL JSONB, then compares query latency for flexible metadata queries.

```bash
docker compose exec api python scripts/benchmark_schema_flexibility.py \
  --source-collection equipment \
  --runs 10 \
  --warmup 3 \
  --output-dir results
```

### Expected benchmark result files

After the completion of all benchmarks, the `results` directory should contain the following files:

```text
results/cassandra_write_consistency.csv
results/graph_depth_latency.png
results/graph_depth_latency_raw.csv
results/graph_depth_latency_summary.csv
results/redis_summary_cache.csv
results/schema_flexibility_raw.csv
results/schema_flexibility_summary.csv
```

## Optional cleanup after testing

Stop containers while keeping database volumes:

```bash
docker compose down --remove-orphans
```

For a full cleanup including database volumes:

```bash
docker compose down -v --remove-orphans
```
