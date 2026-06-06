from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_GRAPH_TOKEN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RELATIONSHIP_TYPE_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


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


class GridNodeCreate(BaseModel):
    node_id: str = Field(..., min_length=1, examples=["TX_001"])
    labels: list[str] = Field(..., min_length=1, examples=[["Transformer"]])
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: list[str]) -> list[str]:
        for label in labels:
            if not _GRAPH_TOKEN_RE.fullmatch(label):
                raise ValueError("Neo4j labels may only contain letters, numbers, and underscores, and must not start with a number")
        return labels


class GridRelationshipCreate(BaseModel):
    source_id: str = Field(..., min_length=1, examples=["SS_001"])
    target_id: str = Field(..., min_length=1, examples=["TX_001"])
    relationship_type: str = Field(..., alias="type", examples=["SUPPLIES"])
    properties: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(populate_by_name=True)

    @field_validator("relationship_type")
    @classmethod
    def validate_relationship_type(cls, relationship_type: str) -> str:
        relationship_type = relationship_type.upper()
        if not _RELATIONSHIP_TYPE_RE.fullmatch(relationship_type):
            raise ValueError("Neo4j relationship types must be uppercase letters, numbers, and underscores, and must not start with a number")
        return relationship_type
