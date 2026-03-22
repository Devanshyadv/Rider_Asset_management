from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, model_validator


class AssetIssuanceCreate(BaseModel):
    riderId: str
    assetId: str
    reconciliationType: str = Field(..., pattern="^(FLAT|EMI)$")
    totalCycles: int = Field(..., ge=1, le=4)

    @field_validator("riderId", "assetId", mode="before")
    @classmethod
    def validate_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v}")

    @model_validator(mode="after")
    def validate_cycles(self):
        if self.reconciliationType == "FLAT" and self.totalCycles != 1:
            raise ValueError("FLAT reconciliation must have totalCycles = 1")
        if self.totalCycles > 4:
            raise ValueError("EMI max cycles = 4 (hard cap)")
        return self


class BulkIssuanceRow(BaseModel):
    riderId: str
    assetId: str
    reconciliationType: str
    totalCycles: int

    @field_validator("riderId", "assetId", mode="before")
    @classmethod
    def validate_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v}")

    @field_validator("totalCycles", mode="before")
    @classmethod
    def validate_cycles(cls, v):
        try:
            v = int(v)
        except (TypeError, ValueError):
            raise ValueError("totalCycles must be an integer")
        if v < 1 or v > 4:
            raise ValueError("totalCycles must be between 1 and 4")
        return v

    @field_validator("reconciliationType", mode="before")
    @classmethod
    def validate_recon_type(cls, v):
        if str(v).upper() not in ("FLAT", "EMI"):
            raise ValueError("reconciliationType must be FLAT or EMI")
        return str(v).upper()


class AssetIssuance(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    riderId: str
    assetId: str
    issuedAt: datetime
    reconciliationType: str
    totalCycles: int
    cyclesCompleted: int = 0
    costPriceSnapshot: float
    absorptionTypeSnapshot: str
    vendorIdSnapshot: Optional[str] = None
    deductionPerCycle: float
    totalRecovered: float = 0.0
    outstandingBalance: float
    status: str = "active"
    replacementCount: int = 0
    replacementOf: Optional[str] = None
    createdAt: datetime

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @field_validator("id", "riderId", "assetId", "vendorIdSnapshot", "replacementOf", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v
