from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class AlertCreate(BaseModel):
    severity: Literal["info", "warning", "critical"] = "info"
    alert_type: str = Field(..., examples=["voltage_drop"])
    source: str = Field(..., examples=["SM_00001"])
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    active: bool = Field(default=True, description="If true, keep the alert in the active-alerts Redis list.")


class AlertOut(AlertCreate):
    alert_id: UUID
    created_at: datetime
