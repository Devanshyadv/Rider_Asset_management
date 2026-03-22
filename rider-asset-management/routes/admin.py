from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from config import get_db
from services.audit_service import AuditService

router = APIRouter(prefix="/api/admin", tags=["Admin Override"])


class WaiveRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    performed_by_user_id: Optional[str] = None


class ManualDeductionAdjust(BaseModel):
    amountDeducted: float = Field(..., ge=0)
    reason: str = Field(..., min_length=1)
    performed_by_user_id: Optional[str] = None


class FlagAbuseRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    performed_by_user_id: Optional[str] = None


class YearEndWriteOffRequest(BaseModel):
    financialYearEnd: datetime = Field(..., description="Financial year-end date")
    performed_by_user_id: Optional[str] = None
    dry_run: bool = Field(False, description="If true, return affected issuances without writing")


@router.post("/waive/{issuance_id}")
async def waive_issuance(
    issuance_id: str,
    payload: WaiveRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Finance: Waive the outstanding balance on an issuance.

    Sets status to 'written_off' and outstanding balance to 0.
    """
    if not ObjectId.is_valid(issuance_id):
        raise HTTPException(status_code=400, detail="Invalid issuance_id")

    oid = ObjectId(issuance_id)
    issuance = await db.asset_issuances.find_one({"_id": oid})
    if not issuance:
        raise HTTPException(status_code=404, detail="Issuance not found")

    if issuance.get("status") in ("completed", "written_off"):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot waive issuance with status '{issuance['status']}'",
        )

    now = datetime.now(timezone.utc)
    before = dict(issuance)

    await db.asset_issuances.update_one(
        {"_id": oid},
        {
            "$set": {
                "status": "written_off",
                "outstandingBalance": 0.0,
                "updatedAt": now,
            }
        },
    )

    # Also mark any pending carry-forwards as recovered
    await db.rider_carry_forwards.update_many(
        {"issuanceId": oid, "status": "pending"},
        {"$set": {"status": "recovered", "updatedAt": now}},
    )

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=issuance_id,
        action="waived",
        performed_by="Finance",
        performed_by_user_id=payload.performed_by_user_id,
        before_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in before.items()},
        after_value={"status": "written_off", "outstandingBalance": 0.0},
        reason=payload.reason,
    )

    return {
        "message": "Issuance waived successfully",
        "issuanceId": issuance_id,
        "previousOutstandingBalance": issuance.get("outstandingBalance", 0.0),
    }


@router.patch("/deduction/{ledger_id}")
async def manual_adjust_deduction(
    ledger_id: str,
    payload: ManualDeductionAdjust,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Finance: Manually adjust the deducted amount on a ledger entry.
    """
    if not ObjectId.is_valid(ledger_id):
        raise HTTPException(status_code=400, detail="Invalid ledger_id")

    oid = ObjectId(ledger_id)
    ledger = await db.asset_deduction_ledger.find_one({"_id": oid})
    if not ledger:
        raise HTTPException(status_code=404, detail="Ledger entry not found")

    now = datetime.now(timezone.utc)
    before = dict(ledger)

    old_deducted = ledger.get("amountDeducted", 0.0)
    amount_due = ledger.get("amountDue", 0.0)
    new_deducted = payload.amountDeducted
    new_shortfall = max(0.0, amount_due - new_deducted)
    new_carried = new_shortfall > 0
    new_status = (
        "deducted"
        if new_deducted >= amount_due
        else ("partial" if new_deducted > 0 else "skipped")
    )

    await db.asset_deduction_ledger.update_one(
        {"_id": oid},
        {
            "$set": {
                "amountDeducted": new_deducted,
                "shortfall": new_shortfall,
                "carriedForward": new_carried,
                "status": new_status,
                "updatedAt": now,
            }
        },
    )

    # Reconcile the issuance's totalRecovered and outstandingBalance
    issuance = await db.asset_issuances.find_one({"_id": ledger["issuanceId"]})
    if issuance:
        delta = new_deducted - old_deducted
        new_recovered = max(0.0, issuance.get("totalRecovered", 0.0) + delta)
        new_outstanding = max(0.0, issuance.get("outstandingBalance", 0.0) - delta)
        await db.asset_issuances.update_one(
            {"_id": issuance["_id"]},
            {
                "$set": {
                    "totalRecovered": new_recovered,
                    "outstandingBalance": new_outstanding,
                    "updatedAt": now,
                }
            },
        )

    audit = AuditService(db)
    await audit.log(
        entity_type="deduction",
        entity_id=ledger_id,
        action="deducted",
        performed_by="Finance",
        performed_by_user_id=payload.performed_by_user_id,
        before_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in before.items()},
        after_value={"amountDeducted": new_deducted, "status": new_status},
        reason=payload.reason,
    )

    updated = await db.asset_deduction_ledger.find_one({"_id": oid})
    updated["_id"] = str(updated["_id"])
    updated["issuanceId"] = str(updated["issuanceId"])
    updated["riderId"] = str(updated["riderId"])
    return updated


@router.post("/flag-abuse/{issuance_id}")
async def flag_abuse(
    issuance_id: str,
    payload: FlagAbuseRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    LMD: Force SELF absorption on an issuance.

    Overrides absorptionTypeSnapshot to SELF so the rider is responsible
    for the outstanding balance instead of the vendor.
    """
    if not ObjectId.is_valid(issuance_id):
        raise HTTPException(status_code=400, detail="Invalid issuance_id")

    oid = ObjectId(issuance_id)
    issuance = await db.asset_issuances.find_one({"_id": oid})
    if not issuance:
        raise HTTPException(status_code=404, detail="Issuance not found")

    if issuance.get("absorptionTypeSnapshot") == "SELF":
        raise HTTPException(
            status_code=400, detail="Issuance already has SELF absorption"
        )

    if issuance.get("status") not in ("active", "vendor_passed"):
        raise HTTPException(
            status_code=400,
            detail="Can only flag abuse on active or vendor_passed issuances",
        )

    now = datetime.now(timezone.utc)
    before = dict(issuance)

    update_fields: Dict[str, Any] = {
        "absorptionTypeSnapshot": "SELF",
        "vendorIdSnapshot": None,
        "updatedAt": now,
    }

    # If it was vendor_passed, reactivate it
    if issuance.get("status") == "vendor_passed":
        update_fields["status"] = "active"
        # Remove any pending vendor liability for this issuance
        await db.vendor_liabilities.update_many(
            {"issuanceId": oid, "status": "pending"},
            {"$set": {"status": "recovered", "updatedAt": now}},
        )

    await db.asset_issuances.update_one({"_id": oid}, {"$set": update_fields})

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=issuance_id,
        action="issued",
        performed_by="LMD",
        performed_by_user_id=payload.performed_by_user_id,
        before_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in before.items()},
        after_value={"absorptionTypeSnapshot": "SELF", "status": update_fields.get("status", issuance.get("status"))},
        reason=f"Abuse flagged — forced SELF absorption. {payload.reason}",
    )

    return {
        "message": "Issuance flagged as abuse — absorption changed to SELF",
        "issuanceId": issuance_id,
        "previousAbsorptionType": issuance.get("absorptionTypeSnapshot"),
    }


@router.post("/year-end-writeoff")
async def year_end_writeoff(
    payload: YearEndWriteOffRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Finance: Bulk write-off SELF-absorption issuances where the rider
    has had no activity since a given financial year-end date.

    Pass dry_run=true to preview without committing changes.
    """
    now = datetime.now(timezone.utc)

    # Find all SELF issuances that are still active
    cursor = db.asset_issuances.find(
        {"absorptionTypeSnapshot": "SELF", "status": "active"}
    )

    to_writeoff: List[str] = []
    async for issuance in cursor:
        rider_oid = issuance["riderId"]
        # Check for any orders after financial year end
        count = await db.orders.count_documents(
            {
                "rider": rider_oid,
                "createdAt": {"$gte": payload.financialYearEnd},
            }
        )
        if count == 0:
            to_writeoff.append(str(issuance["_id"]))

    if payload.dry_run:
        return {
            "dryRun": True,
            "issuancesToWriteOff": to_writeoff,
            "count": len(to_writeoff),
        }

    written_off: List[str] = []
    audit = AuditService(db)

    for issuance_id_str in to_writeoff:
        oid = ObjectId(issuance_id_str)
        issuance = await db.asset_issuances.find_one({"_id": oid})
        if not issuance:
            continue

        before = dict(issuance)

        await db.asset_issuances.update_one(
            {"_id": oid},
            {
                "$set": {
                    "status": "written_off",
                    "outstandingBalance": 0.0,
                    "updatedAt": now,
                }
            },
        )

        # Mark carry-forwards as recovered
        await db.rider_carry_forwards.update_many(
            {"issuanceId": oid, "status": "pending"},
            {"$set": {"status": "recovered", "updatedAt": now}},
        )

        await audit.log(
            entity_type="issuance",
            entity_id=issuance_id_str,
            action="written_off",
            performed_by="Finance",
            performed_by_user_id=payload.performed_by_user_id,
            before_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in before.items()},
            after_value={"status": "written_off", "outstandingBalance": 0.0},
            reason=f"Year-end write-off. Financial year end: {payload.financialYearEnd.isoformat()}",
        )

        written_off.append(issuance_id_str)

    return {
        "message": f"Year-end write-off completed. {len(written_off)} issuances written off.",
        "writtenOff": written_off,
        "count": len(written_off),
    }
