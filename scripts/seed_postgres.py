import asyncio
import json
import os
import uuid
from datetime import date
from decimal import Decimal

import asyncpg


POSTGRES_DSN = os.getenv("POSTGRES_DSN")


def deterministic_uuid(value: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_DNS, value)


async def seed_postgres() -> None:
    if not POSTGRES_DSN:
        raise RuntimeError("POSTGRES_DSN is not set. Check your .env file or Docker env_file.")

    conn = await asyncpg.connect(POSTGRES_DSN)

    try:
        for i in range(1, 101):
            premise_id = f"PREM_{i:04d}"
            consumer_name = f"Consumer {i:03d}"

            tariff_class = ["residential", "commercial", "industrial"][i % 3]

            tariff_rules = {
                "class": tariff_class,
                "currency": "EUR",
                "base_fee": 5.00 if tariff_class == "residential" else 12.50,
                "rate_per_kwh": 0.18 if tariff_class == "residential" else 0.24,
                "peak_multiplier": 1.25 if tariff_class != "residential" else 1.10,
            }

            kwh = Decimal("180.000") + Decimal(i * 3)
            rate = Decimal(str(tariff_rules["rate_per_kwh"]))
            base_fee = Decimal(str(tariff_rules["base_fee"]))
            energy_charge = (kwh * rate).quantize(Decimal("0.01"))
            amount = (energy_charge + base_fee).quantize(Decimal("0.01"))

            current_balance = amount

            await conn.execute(
                """
                INSERT INTO accounts (
                    premise_id,
                    consumer_name,
                    tariff_class,
                    current_balance,
                    tariff_rules
                )
                VALUES ($1, $2, $3, $4, $5::jsonb)
                ON CONFLICT (premise_id)
                DO UPDATE SET
                    consumer_name = EXCLUDED.consumer_name,
                    tariff_class = EXCLUDED.tariff_class,
                    current_balance = EXCLUDED.current_balance,
                    tariff_rules = EXCLUDED.tariff_rules;
                """,
                premise_id,
                consumer_name,
                tariff_class,
                current_balance,
                json.dumps(tariff_rules),
            )

            billing_month = date(2026, 5, 1)
            invoice_id = deterministic_uuid(f"{premise_id}-{billing_month.isoformat()}")

            line_items = [
                {
                    "description": "Energy consumption",
                    "quantity_kwh": float(kwh),
                    "unit_price": float(rate),
                    "amount": float(energy_charge),
                },
                {
                    "description": "Base service fee",
                    "amount": float(base_fee),
                },
            ]

            await conn.execute(
                """
                INSERT INTO invoices (
                    invoice_id,
                    premise_id,
                    billing_month,
                    kwh,
                    amount,
                    line_items
                )
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (premise_id, billing_month)
                DO UPDATE SET
                    kwh = EXCLUDED.kwh,
                    amount = EXCLUDED.amount,
                    line_items = EXCLUDED.line_items;
                """,
                invoice_id,
                premise_id,
                billing_month,
                kwh,
                amount,
                json.dumps(line_items),
            )

        print("PostgreSQL seed complete: 100 accounts and 100 invoices.")

    finally:
        await conn.close()


async def main() -> None:
    await seed_postgres()


if __name__ == "__main__":
    asyncio.run(main())