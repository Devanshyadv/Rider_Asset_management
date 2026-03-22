from __future__ import annotations

from datetime import datetime
from typing import Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator


class AssetDeductionLedger(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    issuanceId: str
    riderId: str
    weekStartDate: datetime
    weekEndDate: datetime
    weekCycle: str  # e.g. "2026-W12"
    amountDue: float
    amountDeducted: float
    shortfall: float
    carriedForward: bool
    status: str  # "deducted" | "partial" | "skipped" | "vendor_passed"
    createdAt: datetime

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @field_validator("id", "issuanceId", "riderId", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v


class DeductionLedgerUpdate(BaseModel):
    amountDeducted: Optional[float] = Field(None, ge=0)
    reason: Optional[str] = None
