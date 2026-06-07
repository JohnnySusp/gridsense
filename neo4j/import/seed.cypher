// -----------------------------------------------------------------------------
// GridSense full Neo4j seed extension
// Creates at least:
// - 10 Substations
// - 40 Transformers
// - 200 SmartMeters
// - FEEDS, SUPPLIES, and CONNECTS_TO relationships
// -----------------------------------------------------------------------------

CREATE CONSTRAINT grid_supply_point_node_id IF NOT EXISTS
FOR (n:GridSupplyPoint)
REQUIRE n.node_id IS UNIQUE;

CREATE CONSTRAINT substation_node_id IF NOT EXISTS
FOR (n:Substation)
REQUIRE n.node_id IS UNIQUE;

CREATE CONSTRAINT transformer_node_id IF NOT EXISTS
FOR (n:Transformer)
REQUIRE n.node_id IS UNIQUE;

CREATE CONSTRAINT smart_meter_node_id IF NOT EXISTS
FOR (n:SmartMeter)
REQUIRE n.node_id IS UNIQUE;

// Root supply point
MERGE (gsp:GridSupplyPoint {node_id: "GSP_001"})
SET
  gsp.name = "North Grid Supply Point",
  gsp.region = "North District",
  gsp.voltage_kV = 132,
  gsp.status = "ACTIVE";

// -----------------------------------------------------------------------------
// 10 substations
// -----------------------------------------------------------------------------
UNWIND range(1, 10) AS i
WITH
  i,
  "SS_" + right("000" + toString(i), 3) AS ss_id
MERGE (s:Substation {node_id: ss_id})
SET
  s.name = "Substation " + right("000" + toString(i), 3),
  s.district = "District " + toString(((i - 1) % 5) + 1),
  s.voltage_kV = 33,
  s.status = "ACTIVE",
  s.asset_class = "substation";

// Connect GSP to each substation
UNWIND range(1, 10) AS i
WITH
  i,
  "SS_" + right("000" + toString(i), 3) AS ss_id,
  "FDR_" + right("000" + toString(i), 3) AS feeder_id
MATCH (gsp:GridSupplyPoint {node_id: "GSP_001"})
MATCH (s:Substation {node_id: ss_id})
MERGE (gsp)-[r:FEEDS {feeder_id: feeder_id}]->(s)
SET
  r.voltage_kV = 33,
  r.length_km = 1.5 + (i * 0.2),
  r.status = "ENERGIZED",
  r.protection_zone = "ZONE_" + toString(((i - 1) % 3) + 1);

// -----------------------------------------------------------------------------
// 40 transformers: 4 per substation
// -----------------------------------------------------------------------------
UNWIND range(1, 40) AS i
WITH
  i,
  toInteger(floor((i - 1) / 4.0)) + 1 AS ss_num,
  "TX_" + right("000" + toString(i), 3) AS tx_id
WITH
  i,
  tx_id,
  "SS_" + right("000" + toString(ss_num), 3) AS ss_id,
  "FDR_" + right("000" + toString(ss_num), 3) AS feeder_id
MATCH (s:Substation {node_id: ss_id})
MERGE (t:Transformer {node_id: tx_id})
SET
  t.name = "Transformer " + right("000" + toString(i), 3),
  t.transformer_id = tx_id,
  t.capacity_kVA = CASE
    WHEN i % 4 = 0 THEN 1000
    WHEN i % 4 = 1 THEN 630
    WHEN i % 4 = 2 THEN 800
    ELSE 500
  END,
  t.phase = "ABC",
  t.status = "ACTIVE",
  t.asset_class = "transformer",
  t.district = s.district
MERGE (s)-[r:SUPPLIES {feeder_id: feeder_id, circuit_id: "CIR_" + right("000" + toString(i), 3)}]->(t)
SET
  r.voltage_kV = 11,
  r.max_load_kW = 450 + (i * 5),
  r.status = "ENERGIZED";

// -----------------------------------------------------------------------------
// 200 smart meters: 5 per transformer
// -----------------------------------------------------------------------------
UNWIND range(1, 200) AS i
WITH
  i,
  toInteger(floor((i - 1) / 5.0)) + 1 AS tx_num,
  "SM_" + right("000" + toString(i), 3) AS meter_id
WITH
  i,
  meter_id,
  "TX_" + right("000" + toString(tx_num), 3) AS tx_id,
  "PREM_" + right("0000" + toString(i), 4) AS premise_id
MATCH (t:Transformer {node_id: tx_id})
MERGE (m:SmartMeter {node_id: meter_id})
SET
  m.name = "Smart Meter " + right("000" + toString(i), 3),
  m.meter_id = meter_id,
  m.premise_id = premise_id,
  m.customer_type = CASE
    WHEN i % 5 = 0 THEN "commercial"
    ELSE "residential"
  END,
  m.manufacturer = CASE
    WHEN i % 3 = 0 THEN "VoltEdge"
    WHEN i % 3 = 1 THEN "GridMeterCo"
    ELSE "AmpereWorks"
  END,
  m.model = CASE
    WHEN i % 3 = 0 THEN "VE-300"
    WHEN i % 3 = 1 THEN "GMC-200"
    ELSE "AW-100"
  END,
  m.status = "ACTIVE",
  m.asset_class = "smart_meter"
MERGE (t)-[r:CONNECTS_TO {service_line_id: "SL_" + right("0000" + toString(i), 4)}]->(m)
SET
  r.phase = CASE
    WHEN i % 3 = 0 THEN "A"
    WHEN i % 3 = 1 THEN "B"
    ELSE "C"
  END,
  r.connected_since = date("2024-01-01"),
  r.status = "ENERGIZED";
