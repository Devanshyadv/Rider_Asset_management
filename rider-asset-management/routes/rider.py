from __future__ import annotations

from typing import Any, Dict, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_db

router = APIRouter(prefix="/api/rider", tags=["Rider View"])


@router.get("/{rider_id}/assets")
async def get_rider_assets(
    rider_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Rider-facing view of their issued assets and outstanding balances.

    Returns:
        {
            "riderId": "...",
            "assets": [
                {
                    "issuanceId": "...",
                    "name": "Delivery Bag",
                    "outstandingBalance": 500,
                    "deductionThisCycle": 250,
                    "cyclesRemaining": 2,
                    "status": "active"
                }
            ],
            "totalOutstanding": 500
        }
    """
    if not ObjectId.is_valid(rider_id):
        raise HTTPException(status_code=400, detail="Invalid rider_id")

    rider_oid = ObjectId(rider_id)

    issuances: List[Dict[str, Any]] = []
    total_outstanding = 0.0

    cursor = db.asset_issuances.find(
        {"riderId": rider_oid, "status": {"$in": ["active", "vendor_passed"]}}
    ).sort("issuedAt", 1)

    async for issuance in cursor:
        asset = await db.asset_master.find_one({"_id": issuance["assetId"]})
        asset_name = asset["name"] if asset else "Unknown Asset"

        cycles_remaining = max(
            0,
            issuance.get("totalCycles", 1) - issuance.get("cyclesCompleted", 0),
        )

        issuances.append(
            {
                "issuanceId": str(issuance["_id"]),
                "assetId": str(issuance["assetId"]),
                "name": asset_name,
                "outstandingBalance": issuance.get("outstandingBalance", 0.0),
                "deductionThisCycle": issuance.get("deductionPerCycle", 0.0),
                "cyclesRemaining": cycles_remaining,
                "totalCycles": issuance.get("totalCycles", 1),
                "cyclesCompleted": issuance.get("cyclesCompleted", 0),
                "absorptionType": issuance.get("absorptionTypeSnapshot"),
                "status": issuance.get("status"),
                "issuedAt": issuance.get("issuedAt").isoformat() if issuance.get("issuedAt") else None,
            }
        )
        total_outstanding += issuance.get("outstandingBalance", 0.0)

    return {
        "riderId": rider_id,
        "assets": issuances,
        "totalOutstanding": round(total_outstanding, 2),
    }
