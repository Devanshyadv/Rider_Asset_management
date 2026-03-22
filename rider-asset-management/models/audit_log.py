from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator


class AssetAuditLog(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    entityType: str  # "issuance" | "deduction" | "vendor_liability" | "replacement"
    entityId: str
    action: str  # "issued" | "deducted" | "carry_forward" | "vendor_passed" | "written_off" | "replaced" | "waived"
    performedBy: str  # "system" | "LMD" | "Finance"
    performedByUserId: Optional[str] = None
    beforeValue: Optional[Dict[str, Any]] = None
    afterValue: Optional[Dict[str, Any]] = None
    reason: Optional[str] = None
    timestamp: datetime

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @field_validator("id", "entityId", "performedByUserId", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v
