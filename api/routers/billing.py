from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.db.postgres import PostgresStore, get_postgres
from api.models.postgres import AccountCreate, AccountOut, AccountUpdate, InvoiceCreate, InvoiceOut

router = APIRouter(prefix="/billing", tags=["Billing"])


def _record(record: asyncpg.Record | None) -> dict[str, Any] | None:
    return dict(record) if record is not None else None


@router.get("/ping")
async def billing_ping() -> dict[str, str]:
    return {"router": "billing", "status": "ok"}


@router.post(
    "/accounts",
    response_model=AccountOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a billing account in PostgreSQL",
)
async def create_account(
    payload: AccountCreate,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
) -> dict[str, Any]:
    pool = postgres.require_pool()
    try:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO accounts (
                  premise_id, consumer_name, tariff_class, current_balance, tariff_rules
                ) VALUES ($1, $2, $3, $4, $5)
                RETURNING premise_id, consumer_name, tariff_class, current_balance, tariff_rules, created_at
                """,
                payload.premise_id,
                payload.consumer_name,
                payload.tariff_class,
                payload.current_balance,
                payload.tariff_rules,
            )
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="premise_id already exists") from exc
    return dict(row)


@router.get(
    "/accounts",
    response_model=list[AccountOut],
    summary="List billing accounts",
)
async def list_accounts(
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
    tariff_class: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    pool = postgres.require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT premise_id, consumer_name, tariff_class, current_balance, tariff_rules, created_at
            FROM accounts
            WHERE ($1::text IS NULL OR tariff_class = $1)
            ORDER BY premise_id
            LIMIT $2 OFFSET $3
            """,
            tariff_class,
            limit,
            offset,
        )
    return [dict(row) for row in rows]


@router.get(
    "/account/{premise_id}",
    response_model=AccountOut,
    summary="Get one billing account by premise_id",
)
@router.get(
    "/accounts/{premise_id}",
    response_model=AccountOut,
    summary="Get one billing account by premise_id",
)
async def get_account(
    premise_id: str,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
) -> dict[str, Any]:
    pool = postgres.require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT premise_id, consumer_name, tariff_class, current_balance, tariff_rules, created_at
            FROM accounts
            WHERE premise_id = $1
            """,
            premise_id,
        )
    account = _record(row)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.patch(
    "/accounts/{premise_id}",
    response_model=AccountOut,
    summary="Patch one billing account by premise_id",
)
async def update_account(
    premise_id: str,
    payload: AccountUpdate,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return await get_account(premise_id, postgres)

    fields = list(updates.keys())
    assignments = ", ".join(f"{field} = ${index}" for index, field in enumerate(fields, start=1))
    values = [updates[field] for field in fields]
    premise_param = len(values) + 1
    query = f"""
        UPDATE accounts
        SET {assignments}
        WHERE premise_id = ${premise_param}
        RETURNING premise_id, consumer_name, tariff_class, current_balance, tariff_rules, created_at
    """

    pool = postgres.require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *values, premise_id)
    account = _record(row)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


async def _create_invoice_transactional(
    payload: InvoiceCreate,
    postgres: PostgresStore,
) -> dict[str, Any]:
    pool = postgres.require_pool()
    try:
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO invoices (
                      invoice_id, premise_id, billing_month, kwh, amount, line_items
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    RETURNING invoice_id, premise_id, billing_month, kwh, amount, line_items, created_at
                    """,
                    payload.invoice_id,
                    payload.premise_id,
                    payload.billing_month,
                    payload.kwh,
                    payload.amount,
                    payload.line_items,
                )
                updated = await conn.fetchrow(
                    """
                    UPDATE accounts
                    SET current_balance = current_balance + $1
                    WHERE premise_id = $2
                    RETURNING premise_id
                    """,
                    payload.amount,
                    payload.premise_id,
                )
                if updated is None:
                    # The foreign key should catch this first, but this keeps the
                    # transaction safe even if schema constraints are missing.
                    raise HTTPException(status_code=404, detail="Account for premise_id does not exist")
    except asyncpg.ForeignKeyViolationError as exc:
        raise HTTPException(status_code=404, detail="Account for premise_id does not exist") from exc
    except asyncpg.UniqueViolationError as exc:
        raise HTTPException(status_code=409, detail="Invoice already exists for this premise and month") from exc
    return dict(row)


@router.post(
    "/invoice",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Generate a monthly invoice in an ACID PostgreSQL transaction",
)
@router.post(
    "/invoices",
    response_model=InvoiceOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a billing invoice in PostgreSQL",
)
async def create_invoice(
    payload: InvoiceCreate,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
) -> dict[str, Any]:
    return await _create_invoice_transactional(payload, postgres)


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceOut,
    summary="Get one invoice by invoice_id",
)
async def get_invoice(
    invoice_id: UUID,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
) -> dict[str, Any]:
    pool = postgres.require_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT invoice_id, premise_id, billing_month, kwh, amount, line_items, created_at
            FROM invoices
            WHERE invoice_id = $1
            """,
            invoice_id,
        )
    invoice = _record(row)
    if invoice is None:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return invoice


@router.get(
    "/accounts/{premise_id}/invoices",
    response_model=list[InvoiceOut],
    summary="List invoices for one billing account",
)
async def list_account_invoices(
    premise_id: str,
    postgres: Annotated[PostgresStore, Depends(get_postgres)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    pool = postgres.require_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT invoice_id, premise_id, billing_month, kwh, amount, line_items, created_at
            FROM invoices
            WHERE premise_id = $1
            ORDER BY billing_month DESC
            LIMIT $2 OFFSET $3
            """,
            premise_id,
            limit,
            offset,
        )
    return [dict(row) for row in rows]
