CREATE KEYSPACE IF NOT EXISTS gridsense
WITH replication = {
  'class': 'SimpleStrategy',
  'replication_factor': 1
};

USE gridsense;

CREATE TABLE IF NOT EXISTS sensor_readings (
  sensor_id TEXT,
  sensor_type TEXT,
  bucket_day DATE,
  reading_time TIMESTAMP,
  reading_id TIMEUUID,
  voltage DOUBLE,
  current DOUBLE,
  power_factor DOUBLE,
  temperature DOUBLE,
  quality_flag TINYINT,
  PRIMARY KEY ((sensor_id, bucket_day), reading_time, reading_id)
) WITH CLUSTERING ORDER BY (reading_time DESC, reading_id DESC)
  AND default_time_to_live = 7776000;

CREATE TABLE IF NOT EXISTS relay_events (
  feeder_id TEXT,
  event_time TIMEUUID,
  relay_id TEXT,
  event_type TEXT,
  fault_type TEXT,
  current_kA FLOAT,
  PRIMARY KEY ((feeder_id), event_time)
) WITH CLUSTERING ORDER BY (event_time ASC);

CREATE TABLE IF NOT EXISTS sensor_readings_by_bucket (
  bucket_start TIMESTAMP,
  shard SMALLINT,
  sensor_id TEXT,
  sensor_type TEXT,
  reading_time TIMESTAMP,
  reading_id TIMEUUID,
  voltage DOUBLE,
  current DOUBLE,
  power_factor DOUBLE,
  temperature DOUBLE,
  quality_flag TINYINT,
  PRIMARY KEY ((bucket_start, shard), reading_time, reading_id, sensor_id)
) WITH CLUSTERING ORDER BY (reading_time DESC, reading_id DESC, sensor_id ASC)
  AND default_time_to_live = 7776000;
