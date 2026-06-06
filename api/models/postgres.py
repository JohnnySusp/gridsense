from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    premise_id: str = Field(..., min_length=1, examples=["PREM_10001"])
    consumer_name: str = Field(..., min_length=1, examples=["Alex Papadopoulos"])
    tariff_class: str = Field(..., min_length=1, examples=["residential"])
    current_balance: Decimal = Field(default=Decimal("0.00"))
    tariff_rules: dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    consumer_name: str | None = None
    tariff_class: str | None = None
    current_balance: Decimal | None = None
    tariff_rules: dict[str, Any] | None = None


class AccountOut(AccountCreate):
    created_at: datetime


class InvoiceCreate(BaseModel):
    invoice_id: UUID = Field(default_factory=uuid4)
    premise_id: str = Field(..., min_length=1, examples=["PREM_10001"])
    billing_month: date = Field(..., description="Use the first day of the billing month.")
    kwh: Decimal = Field(..., ge=0)
    amount: Decimal = Field(..., ge=0)
    line_items: list[dict[str, Any]] = Field(default_factory=list)


class InvoiceOut(InvoiceCreate):
    created_at: datetime
