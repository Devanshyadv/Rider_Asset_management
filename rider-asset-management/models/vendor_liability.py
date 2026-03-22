from __future__ import annotations

from datetime import datetime
from typing import Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator


class VendorLiability(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    vendorId: str
    issuanceId: str
    riderId: str
    outstandingAmount: float
    amountRecovered: float = 0.0
    status: str = "pending"  # "pending" | "partially_recovered" | "recovered"
    passedAt: datetime
    weekCycle: str  # e.g. "2026-W12"
    createdAt: datetime

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @field_validator("id", "vendorId", "issuanceId", "riderId", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v


class VendorSettleRequest(BaseModel):
    amountPaid: float = Field(..., gt=0)
    note: Optional[str] = None
