from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from bson import ObjectId
from config import get_db
from models.vendor_liability import VendorSettleRequest
from services.vendor_service import VendorService

router = APIRouter(prefix="/api/vendors", tags=["Vendors"])


@router.get("/{vendor_id}/liabilities")
async def get_vendor_liabilities(
    vendor_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Get all outstanding liabilities for a vendor."""
    if not ObjectId.is_valid(vendor_id):
        raise HTTPException(status_code=400, detail="Invalid vendor_id")

    service = VendorService(db)
    liabilities = await service.get_liabilities(vendor_id)
    return liabilities


@router.post("/{vendor_id}/settle")
async def settle_vendor(
    vendor_id: str,
    payload: VendorSettleRequest,
    performed_by_user_id: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Record a vendor payout adjustment.

    Applies the payment to oldest outstanding liabilities first (FIFO).
    """
    if not ObjectId.is_valid(vendor_id):
        raise HTTPException(status_code=400, detail="Invalid vendor_id")

    service = VendorService(db)
    result = await service.settle_vendor(
        vendor_id_str=vendor_id,
        amount_paid=payload.amountPaid,
        note=payload.note,
        performed_by_user_id=performed_by_user_id,
    )
    return result
