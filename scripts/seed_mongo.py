#!/usr/bin/env python3
import os
from datetime import datetime, timezone
from pymongo import MongoClient, ReplaceOne, ASCENDING


def build_non_standard_telemetry_fields(vendor: str) -> dict:
    metric_names = [
        "harmonic_distortion_l1", "harmonic_distortion_l2", "harmonic_distortion_l3",
        "neutral_current", "tamper_signal_strength", "magnetic_tamper_score",
        "cover_open_count", "reverse_energy_kwh", "phase_angle_l1", "phase_angle_l2",
        "phase_angle_l3", "sag_event_count", "swell_event_count", "brownout_seconds",
        "last_outage_duration_s", "internal_battery_mv", "meter_case_temperature",
        "terminal_temperature", "rf_noise_floor_dbm", "plc_signal_quality",
        "clock_drift_ppm", "firmware_crc", "load_profile_slot_count",
        "demand_response_state", "relay_contact_resistance", "neutral_voltage",
        "frequency_deviation_hz", "phase_sequence_error", "ct_ratio_detected",
        "pt_ratio_detected", "peak_demand_kw", "minimum_voltage_24h",
        "maximum_voltage_24h", "event_log_depth", "optical_port_access_count",
        "encryption_key_version", "meter_tilt_angle", "humidity_inside_case",
        "last_calibration_offset", "manufacturer_diag_code"
    ]

    fields = {}
    for i, name in enumerate(metric_names, start=1):
        fields[name] = {
            "vendor_field_id": f"{vendor.upper()}_{i:02d}",
            "type": "float" if i % 5 != 0 else "string",
            "unit": "varies",
            "description": f"Vendor-specific telemetry field {i} for {vendor}"
        }
    return fields


def build_documents() -> list[dict]:
    now = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    docs: list[dict] = []

    # 10 substation documents.
    for i in range(1, 11):
        docs.append({
            "asset_id": f"SS_{i:03d}",
            "equipment_type": "substation",
            "name": f"Substation {i:03d}",
            "manufacturer": ["ABB", "Siemens", "Schneider Electric"][i % 3],
            "commissioned_year": 2005 + i,
            "district": ["North", "East", "South", "West", "Central"][i % 5],
            "status": "operational" if i != 7 else "maintenance",
            "voltage_profile": {
                "primary_kv": 33,
                "secondary_kv": 11,
                "frequency_hz": 50
            },
            "switchgear": {
                "type": "gas_insulated" if i % 2 == 0 else "air_insulated",
                "breaker_count": 4 + (i % 4),
                "arc_flash_rating": "medium"
            },
            "protection": {
                "relay_family": "SEL" if i % 2 else "ABB Relion",
                "has_remote_trip": True
            },
            "last_inspection": f"2025-{(i % 12) + 1:02d}-15",
            "created_at": now,
            "updated_at": now
        })

    # 10 transformer documents.
    for i in range(1, 11):
        docs.append({
            "asset_id": f"TX_{i:03d}",
            "equipment_type": "transformer",
            "substation_id": f"SS_{((i - 1) % 10) + 1:03d}",
            "manufacturer": ["ABB", "Eaton", "Siemens", "Hitachi Energy"][i % 4],
            "model": f"ONAN-{250 + i * 50}",
            "rating_kva": 250 + i * 50,
            "phases": 3,
            "cooling_type": "ONAN" if i % 2 else "ONAF",
            "installation": {
                "installed_on": f"201{ i % 10 }-06-15",
                "mounting": "pad" if i % 2 else "pole",
                "indoor": False
            },
            "oil_test": {
                "last_test": f"2025-{(i % 12) + 1:02d}-03",
                "moisture_ppm": 8 + i,
                "dissolved_gas_alarm": i in [4, 9]
            },
            "thermal_limits": {
                "normal_load_pct": 85,
                "overload_alarm_pct": 110,
                "trip_pct": 135
            },
            "created_at": now,
            "updated_at": now
        })

    # 10 smart-meter documents with 40 non-standard telemetry field definitions.
    for i in range(1, 11):
        vendor = ["LandisGyr", "Itron", "Kamstrup", "Elster"][i % 4]
        docs.append({
            "asset_id": f"SM_{i:03d}",
            "equipment_type": "smart_meter",
            "meter_id": f"SM_{i:03d}",
            "premise_id": f"PREM_{i:04d}",
            "manufacturer": vendor,
            "model": f"{vendor}-GX-{100 + i}",
            "firmware_version": f"3.{i % 4}.{i}",
            "rated_voltage": 230 + (i % 3) * 5,
            "phase": "single" if i % 3 else "three",
            "communication": {
                "protocol": ["DLMS/COSEM", "PLC", "LTE-M"][i % 3],
                "signal_quality": "good" if i % 4 else "weak",
                "last_seen": f"2026-06-{(i % 28) + 1:02d}T12:00:00Z"
            },
            "standard_metrics": [
                "voltage",
                "current",
                "power_factor",
                "temperature"
            ],
            "non_standard_telemetry_fields": build_non_standard_telemetry_fields(vendor),
            "security": {
                "secure_boot": True,
                "key_version": f"k{i % 3}",
                "tamper_detection": True
            },
            "created_at": now,
            "updated_at": now
        })

    return docs


def main() -> None:
    mongo_uri = os.getenv("MONGO_URI")
    mongo_db = os.getenv("MONGO_DB", "gridsense")

    if not mongo_uri:
        raise RuntimeError("MONGO_URI is not set. Check your .env file and docker-compose env_file.")

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=10000)
    client.admin.command("ping")

    db = client[mongo_db]
    equipment = db.equipment

    equipment.create_index([("asset_id", ASCENDING)], unique=True)
    equipment.create_index([("equipment_type", ASCENDING)])
    equipment.create_index([("manufacturer", ASCENDING)])
    equipment.create_index([("firmware_version", ASCENDING)])
    equipment.create_index([("rated_voltage", ASCENDING)])

    docs = build_documents()

    operations = [
        ReplaceOne(
            {"asset_id": doc["asset_id"]},
            doc,
            upsert=True
        )
        for doc in docs
    ]

    result = equipment.bulk_write(operations, ordered=False)

    print("MongoDB equipment load complete.")
    print(f"Database: {mongo_db}")
    print(f"Collection: equipment")
    print(f"Input documents: {len(docs)}")
    print(f"Upserted: {result.upserted_count}")
    print(f"Matched existing: {result.matched_count}")
    print(f"Modified existing: {result.modified_count}")

    print("Counts by equipment_type:")
    for row in equipment.aggregate([
        {"$group": {"_id": "$equipment_type", "count": {"$sum": 1}}},
        {"$sort": {"_id": 1}}
    ]):
        print(f"  {row['_id']}: {row['count']}")

    print(f"Total equipment records: {equipment.count_documents({})}")


if __name__ == "__main__":
    main()
