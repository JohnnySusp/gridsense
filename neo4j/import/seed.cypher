CREATE CONSTRAINT gsp_node_id IF NOT EXISTS
FOR (g:GridSupplyPoint) REQUIRE g.node_id IS UNIQUE;

CREATE CONSTRAINT substation_node_id IF NOT EXISTS
FOR (s:Substation) REQUIRE s.node_id IS UNIQUE;

CREATE CONSTRAINT transformer_node_id IF NOT EXISTS
FOR (t:Transformer) REQUIRE t.node_id IS UNIQUE;

CREATE CONSTRAINT meter_node_id IF NOT EXISTS
FOR (m:SmartMeter) REQUIRE m.node_id IS UNIQUE;

MERGE (g:GridSupplyPoint {node_id: "GSP_NORTH"})
SET g.gsp_id = "GSP_NORTH",
    g.name = "Northern Grid Supply Point",
    g.voltage_kV = 132,
    g.region = "North Metro";

MERGE (s1:Substation {node_id: "SS_001"})
SET s1.substation_id = "SS_001",
    s1.name = "Volos Primary",
    s1.voltage_kV = 11,
    s1.lat = 39.358,
    s1.lon = 22.938,
    s1.commissioned_year = 1998;

MERGE (s2:Substation {node_id: "SS_002"})
SET s2.substation_id = "SS_002",
    s2.name = "Nea Ionia Substation",
    s2.voltage_kV = 11,
    s2.lat = 39.379,
    s2.lon = 22.925,
    s2.commissioned_year = 2004;

MERGE (s3:Substation {node_id: "SS_003"})
SET s3.substation_id = "SS_003",
    s3.name = "Port District Substation",
    s3.voltage_kV = 11,
    s3.lat = 39.361,
    s3.lon = 22.944,
    s3.commissioned_year = 2011;

MERGE (t1:Transformer {node_id: "TX_001_A"})
SET t1.asset_id = "TX_001_A",
    t1.rating_kVA = 400,
    t1.manufacturer = "ABB",
    t1.model = "ONAN-400";

MERGE (t2:Transformer {node_id: "TX_001_B"})
SET t2.asset_id = "TX_001_B",
    t2.rating_kVA = 630,
    t2.manufacturer = "Siemens",
    t2.model = "S-630";

MERGE (t3:Transformer {node_id: "TX_002_A"})
SET t3.asset_id = "TX_002_A",
    t3.rating_kVA = 250,
    t3.manufacturer = "Schneider",
    t3.model = "TR-250";

MERGE (t4:Transformer {node_id: "TX_002_B"})
SET t4.asset_id = "TX_002_B",
    t4.rating_kVA = 400,
    t4.manufacturer = "ABB",
    t4.model = "ONAN-400";

MERGE (t5:Transformer {node_id: "TX_003_A"})
SET t5.asset_id = "TX_003_A",
    t5.rating_kVA = 800,
    t5.manufacturer = "Siemens",
    t5.model = "S-800";

MERGE (t6:Transformer {node_id: "TX_003_B"})
SET t6.asset_id = "TX_003_B",
    t6.rating_kVA = 315,
    t6.manufacturer = "Schneider",
    t6.model = "TR-315";

MERGE (m1:SmartMeter {node_id: "SM_00001"})
SET m1.meter_id = "SM_00001", m1.premise_id = "PREM_10001", m1.tariff_class = "residential", m1.phase = "single";

MERGE (m2:SmartMeter {node_id: "SM_00002"})
SET m2.meter_id = "SM_00002", m2.premise_id = "PREM_10002", m2.tariff_class = "residential", m2.phase = "single";

MERGE (m3:SmartMeter {node_id: "SM_00003"})
SET m3.meter_id = "SM_00003", m3.premise_id = "PREM_10003", m3.tariff_class = "commercial", m3.phase = "three";

MERGE (m4:SmartMeter {node_id: "SM_00004"})
SET m4.meter_id = "SM_00004", m4.premise_id = "PREM_10004", m4.tariff_class = "residential", m4.phase = "single";

MERGE (m5:SmartMeter {node_id: "SM_00005"})
SET m5.meter_id = "SM_00005", m5.premise_id = "PREM_10005", m5.tariff_class = "commercial", m5.phase = "three";

MERGE (m6:SmartMeter {node_id: "SM_00006"})
SET m6.meter_id = "SM_00006", m6.premise_id = "PREM_10006", m6.tariff_class = "residential", m6.phase = "single";

MERGE (m7:SmartMeter {node_id: "SM_00007"})
SET m7.meter_id = "SM_00007", m7.premise_id = "PREM_10007", m7.tariff_class = "residential", m7.phase = "single";

MERGE (m8:SmartMeter {node_id: "SM_00008"})
SET m8.meter_id = "SM_00008", m8.premise_id = "PREM_10008", m8.tariff_class = "commercial", m8.phase = "three";

MERGE (m9:SmartMeter {node_id: "SM_00009"})
SET m9.meter_id = "SM_00009", m9.premise_id = "PREM_10009", m9.tariff_class = "residential", m9.phase = "single";

MERGE (m10:SmartMeter {node_id: "SM_00010"})
SET m10.meter_id = "SM_00010", m10.premise_id = "PREM_10010", m10.tariff_class = "residential", m10.phase = "single";

MERGE (m11:SmartMeter {node_id: "SM_00011"})
SET m11.meter_id = "SM_00011", m11.premise_id = "PREM_10011", m11.tariff_class = "commercial", m11.phase = "three";

MERGE (m12:SmartMeter {node_id: "SM_00012"})
SET m12.meter_id = "SM_00012", m12.premise_id = "PREM_10012", m12.tariff_class = "residential", m12.phase = "single";

MERGE (g)-[:FEEDS {feeder_id:"F_001", voltage_kV:11, length_km:2.4}]->(s1);
MERGE (g)-[:FEEDS {feeder_id:"F_002", voltage_kV:11, length_km:3.1}]->(s2);
MERGE (g)-[:FEEDS {feeder_id:"F_003", voltage_kV:11, length_km:1.7}]->(s3);

MERGE (s1)-[:SUPPLIES {cable_id:"CB_001", distance_m:320}]->(t1);
MERGE (s1)-[:SUPPLIES {cable_id:"CB_002", distance_m:410}]->(t2);
MERGE (s2)-[:SUPPLIES {cable_id:"CB_003", distance_m:270}]->(t3);
MERGE (s2)-[:SUPPLIES {cable_id:"CB_004", distance_m:530}]->(t4);
MERGE (s3)-[:SUPPLIES {cable_id:"CB_005", distance_m:190}]->(t5);
MERGE (s3)-[:SUPPLIES {cable_id:"CB_006", distance_m:360}]->(t6);

MERGE (t1)-[:CONNECTS_TO]->(m1);
MERGE (t1)-[:CONNECTS_TO]->(m2);
MERGE (t2)-[:CONNECTS_TO]->(m3);
MERGE (t2)-[:CONNECTS_TO]->(m4);
MERGE (t3)-[:CONNECTS_TO]->(m5);
MERGE (t3)-[:CONNECTS_TO]->(m6);
MERGE (t4)-[:CONNECTS_TO]->(m7);
MERGE (t4)-[:CONNECTS_TO]->(m8);
MERGE (t5)-[:CONNECTS_TO]->(m9);
MERGE (t5)-[:CONNECTS_TO]->(m10);
MERGE (t6)-[:CONNECTS_TO]->(m11);
MERGE (t6)-[:CONNECTS_TO]->(m12);
