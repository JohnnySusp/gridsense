from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class EquipmentBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    asset_id: str = Field(..., min_length=1, examples=["TX_001"])
    equipment_type: str = Field(..., min_length=1, examples=["transformer"])
    manufacturer: str | None = Field(default=None, examples=["ABB"])
    model: str | None = Field(default=None, examples=["ONAN-400"])
    status: str = Field(default="active", examples=["active"])
    substation_id: str | None = Field(default=None, examples=["SS_001"])
    installed_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class EquipmentCreate(EquipmentBase):
    pass


class EquipmentUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    equipment_type: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    status: str | None = None
    substation_id: str | None = None
    installed_at: datetime | None = None
    metadata: dict[str, Any] | None = None


class EquipmentOut(EquipmentBase):
    id: str | None = None

    model_config = ConfigDict(from_attributes=True, extra="allow")