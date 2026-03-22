from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from bson import ObjectId
from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Custom ObjectId type compatible with Pydantic v2
# ---------------------------------------------------------------------------

class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v: Any) -> str:
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str):
            if ObjectId.is_valid(v):
                return v
            raise ValueError(f"Invalid ObjectId: {v}")
        raise TypeError(f"ObjectId or str required, got {type(v)}")

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):
        from pydantic_core import core_schema

        return core_schema.no_info_plain_validator_function(
            cls._validate,
            serialization=core_schema.to_string_ser_schema(),
        )

    @classmethod
    def _validate(cls, v: Any) -> str:
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str):
            if ObjectId.is_valid(v):
                return v
            raise ValueError(f"Invalid ObjectId string: {v}")
        raise TypeError(f"ObjectId or str required, got {type(v)}")


# ---------------------------------------------------------------------------
# AssetMaster models
# ---------------------------------------------------------------------------

class AssetMasterCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    costPrice: float = Field(..., gt=0)
    absorptionType: str = Field(..., pattern="^(VENDOR|SELF)$")
    vendorId: Optional[str] = Field(None)
    stock: int = Field(..., ge=0)

    @field_validator("vendorId", mode="before")
    @classmethod
    def validate_vendor_id(cls, v):
        if v is None:
            return v
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid vendorId: {v}")

    @model_validator(mode="after")
    def vendor_required_for_vendor_type(self):
        if self.absorptionType == "VENDOR" and not self.vendorId:
            raise ValueError("vendorId is required when absorptionType is VENDOR")
        return self


class AssetMasterUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    costPrice: Optional[float] = Field(None, gt=0)
    absorptionType: Optional[str] = Field(None, pattern="^(VENDOR|SELF)$")
    vendorId: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(active|inactive)$")
    stock: Optional[int] = Field(None, ge=0)

    @field_validator("vendorId", mode="before")
    @classmethod
    def validate_vendor_id(cls, v):
        if v is None:
            return v
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid vendorId: {v}")


class AssetMaster(BaseModel):
    id: Optional[str] = Field(None, alias="_id")
    name: str
    costPrice: float
    absorptionType: str
    vendorId: Optional[str] = None
    status: str = "active"
    stock: int
    createdAt: datetime
    updatedAt: datetime

    model_config = {"populate_by_name": True, "arbitrary_types_allowed": True}

    @field_validator("id", "vendorId", mode="before")
    @classmethod
    def convert_objectid(cls, v):
        if isinstance(v, ObjectId):
            return str(v)
        return v
