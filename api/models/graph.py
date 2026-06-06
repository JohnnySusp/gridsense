from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GridNode(BaseModel):
    node_id: str | None = None
    labels: list[str]
    properties: dict[str, Any]


class GridRelationship(BaseModel):
    source: str | None = None
    target: str | None = None
    type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class GridPath(BaseModel):
    nodes: list[GridNode]
    relationships: list[GridRelationship]
