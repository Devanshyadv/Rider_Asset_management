from __future__ import annotations

import io
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
from bson import ObjectId
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_db
from models.issuance import AssetIssuanceCreate, BulkIssuanceRow
from services.audit_service import AuditService
from services.deduction_calculator import DeductionCalculator

router = APIRouter(prefix="/api/issuances", tags=["Issuances"])


def _serialize_issuance(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["_id"] = str(doc["_id"])
    for field in ("riderId", "assetId", "vendorIdSnapshot", "replacementOf"):
        if doc.get(field):
            doc[field] = str(doc[field])
    return doc


async def _do_issue_asset(
    db: AsyncIOMotorDatabase,
    payload: AssetIssuanceCreate,
    confirm: bool = False,
) -> Dict[str, Any]:
    """
    Core issuance logic — shared by single and bulk endpoints.
    Raises HTTPException on hard blocks.
    Returns the inserted issuance doc (serialized).
    """
    rider_oid = ObjectId(payload.riderId)
    asset_oid = ObjectId(payload.assetId)

    # --- Fetch asset ---
    asset = await db.asset_master.find_one({"_id": asset_oid})
    if not asset:
        raise HTTPException(status_code=404, detail="Asset not found")

    # Hard block: asset must be active
    if asset.get("status") != "active":
        raise HTTPException(
            status_code=400, detail="Asset is not active and cannot be issued"
        )

    # Hard block: stock must be > 0
    if asset.get("stock", 0) <= 0:
        raise HTTPException(
            status_code=400, detail="Asset is out of stock"
        )

    # Hard block: cycles must be <= 4
    if payload.totalCycles > 4:
        raise HTTPException(
            status_code=400, detail="totalCycles cannot exceed 4 (hard cap)"
        )

    # Duplicate check — same rider + same asset + active
    existing_issuance = await db.asset_issuances.find_one(
        {"riderId": rider_oid, "assetId": asset_oid, "status": "active"}
    )
    if existing_issuance and not confirm:
        return {
            "warning": "Duplicate issuance detected for rider+asset. Pass confirm=true to proceed.",
            "existingIssuanceId": str(existing_issuance["_id"]),
        }

    now = datetime.now(timezone.utc)
    deduction_per_cycle = DeductionCalculator.compute_deduction_per_cycle(
        asset["costPrice"], payload.totalCycles
    )

    issuance_doc = {
        "riderId": rider_oid,
        "assetId": asset_oid,
        "issuedAt": now,
        "reconciliationType": payload.reconciliationType,
        "totalCycles": payload.totalCycles,
        "cyclesCompleted": 0,
        "costPriceSnapshot": asset["costPrice"],
        "absorptionTypeSnapshot": asset["absorptionType"],
        "vendorIdSnapshot": asset.get("vendorId"),
        "deductionPerCycle": deduction_per_cycle,
        "totalRecovered": 0.0,
        "outstandingBalance": float(asset["costPrice"]),
        "status": "active",
        "replacementCount": 0,
        "replacementOf": None,
        "createdAt": now,
    }

    if issuance_doc["vendorIdSnapshot"] and isinstance(issuance_doc["vendorIdSnapshot"], str):
        issuance_doc["vendorIdSnapshot"] = ObjectId(issuance_doc["vendorIdSnapshot"])

    result = await db.asset_issuances.insert_one(issuance_doc)

    # Decrement stock
    await db.asset_master.update_one(
        {"_id": asset_oid},
        {"$inc": {"stock": -1}, "$set": {"updatedAt": now}},
    )

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=str(result.inserted_id),
        action="issued",
        performed_by="LMD",
        after_value={
            "riderId": str(rider_oid),
            "assetId": str(asset_oid),
            "costPrice": asset["costPrice"],
            "deductionPerCycle": deduction_per_cycle,
            "totalCycles": payload.totalCycles,
        },
        reason="Asset issued to rider",
    )

    created = await db.asset_issuances.find_one({"_id": result.inserted_id})
    return _serialize_issuance(created)


@router.post("", status_code=status.HTTP_201_CREATED)
async def issue_asset(
    payload: AssetIssuanceCreate,
    confirm: bool = Query(False, description="Pass true to confirm duplicate issuance"),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """Issue an asset to a rider."""
    return await _do_issue_asset(db, payload, confirm=confirm)


@router.get("/{rider_id}")
async def get_rider_issuances(
    rider_id: str,
    status_filter: Optional[str] = Query(None, alias="status"),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Get all issuances for a specific rider."""
    if not ObjectId.is_valid(rider_id):
        raise HTTPException(status_code=400, detail="Invalid rider_id")

    query: Dict[str, Any] = {"riderId": ObjectId(rider_id)}
    if status_filter:
        query["status"] = status_filter

    issuances = []
    async for doc in db.asset_issuances.find(query).sort("issuedAt", -1):
        issuances.append(_serialize_issuance(doc))
    return issuances


@router.post("/bulk", status_code=status.HTTP_200_OK)
async def bulk_issue_assets(
    file: UploadFile = File(...),
    confirm: bool = Query(False),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Bulk upload issuances via CSV file.

    Expected CSV columns: riderId, assetId, reconciliationType, totalCycles

    Returns:
        {
            "processed": N,
            "rejected": [{"row": <row_index>, "data": {...}, "reason": "..."}]
        }
    """
    content = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse CSV: {exc}")

    required_cols = {"riderId", "assetId", "reconciliationType", "totalCycles"}
    if not required_cols.issubset(set(df.columns)):
        raise HTTPException(
            status_code=400,
            detail=f"CSV must contain columns: {required_cols}",
        )

    processed = 0
    rejected: List[Dict[str, Any]] = []

    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        try:
            row_obj = BulkIssuanceRow(**row_dict)
        except Exception as exc:
            rejected.append({"row": int(idx), "data": row_dict, "reason": str(exc)})
            continue

        payload = AssetIssuanceCreate(
            riderId=row_obj.riderId,
            assetId=row_obj.assetId,
            reconciliationType=row_obj.reconciliationType,
            totalCycles=row_obj.totalCycles,
        )

        try:
            result = await _do_issue_asset(db, payload, confirm=confirm)
            if "warning" in result:
                rejected.append(
                    {"row": int(idx), "data": row_dict, "reason": result["warning"]}
                )
            else:
                processed += 1
        except HTTPException as exc:
            rejected.append(
                {"row": int(idx), "data": row_dict, "reason": exc.detail}
            )
        except Exception as exc:
            rejected.append({"row": int(idx), "data": row_dict, "reason": str(exc)})

    return {"processed": processed, "rejected": rejected}


@router.post("/{issuance_id}/replace", status_code=status.HTTP_201_CREATED)
async def replace_issuance(
    issuance_id: str,
    mid_balance_action: str = Query(
        ..., pattern="^(waive_remaining|pass_to_vendor)$",
        description="What to do with remaining balance: waive_remaining or pass_to_vendor"
    ),
    performed_by_user_id: Optional[str] = Query(None),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    LMD triggers replacement of an active issuance.

    Stops deductions on the original, creates a new issuance with a fresh
    deduction plan, and handles the mid-EMI balance per the chosen action.
    """
    if not ObjectId.is_valid(issuance_id):
        raise HTTPException(status_code=400, detail="Invalid issuance_id")

    oid = ObjectId(issuance_id)
    original = await db.asset_issuances.find_one({"_id": oid})
    if not original:
        raise HTTPException(status_code=404, detail="Issuance not found")

    if original.get("status") != "active":
        raise HTTPException(
            status_code=400, detail="Only active issuances can be replaced"
        )

    # Check replacement cap (max 2 replacements per rider for this asset)
    existing_replacements = await db.asset_issuances.count_documents(
        {
            "riderId": original["riderId"],
            "assetId": original["assetId"],
            "replacementOf": {"$ne": None},
        }
    )
    if existing_replacements >= 2:
        raise HTTPException(
            status_code=400,
            detail="Maximum 2 replacements per rider for this asset (LMD can override via admin API)",
        )

    asset = await db.asset_master.find_one({"_id": original["assetId"]})
    if not asset:
        raise HTTPException(status_code=404, detail="Associated asset not found")

    if asset.get("stock", 0) <= 0:
        raise HTTPException(status_code=400, detail="Asset out of stock for replacement")

    now = datetime.now(timezone.utc)
    remaining_balance = original.get("outstandingBalance", 0.0)

    audit = AuditService(db)

    # --- Handle mid-EMI balance ---
    if mid_balance_action == "waive_remaining":
        await db.asset_issuances.update_one(
            {"_id": oid},
            {"$set": {"status": "replaced", "outstandingBalance": 0.0, "updatedAt": now}},
        )
        await audit.log(
            entity_type="replacement",
            entity_id=issuance_id,
            action="waived",
            performed_by="LMD",
            performed_by_user_id=performed_by_user_id,
            before_value={"outstandingBalance": remaining_balance, "status": "active"},
            after_value={"status": "replaced", "outstandingBalance": 0.0},
            reason="Replacement — remaining balance waived",
        )
    elif mid_balance_action == "pass_to_vendor":
        vendor_id = original.get("vendorIdSnapshot")
        if not vendor_id:
            raise HTTPException(
                status_code=400,
                detail="Cannot pass to vendor: no vendorIdSnapshot on issuance",
            )

        week_cycle = f"{now.isocalendar()[0]}-W{now.isocalendar()[1]:02d}"
        liability_doc = {
            "vendorId": vendor_id,
            "issuanceId": oid,
            "riderId": original["riderId"],
            "outstandingAmount": remaining_balance,
            "amountRecovered": 0.0,
            "status": "pending",
            "passedAt": now,
            "weekCycle": week_cycle,
            "createdAt": now,
        }
        await db.vendor_liabilities.insert_one(liability_doc)
        await db.asset_issuances.update_one(
            {"_id": oid},
            {"$set": {"status": "replaced", "updatedAt": now}},
        )
        await audit.log(
            entity_type="replacement",
            entity_id=issuance_id,
            action="vendor_passed",
            performed_by="LMD",
            performed_by_user_id=performed_by_user_id,
            before_value={"outstandingBalance": remaining_balance, "status": "active"},
            after_value={"status": "replaced", "midBalancePassedToVendor": remaining_balance},
            reason="Replacement — remaining balance passed to vendor",
        )

    # --- Create new issuance ---
    deduction_per_cycle = DeductionCalculator.compute_deduction_per_cycle(
        asset["costPrice"], original["totalCycles"]
    )

    new_issuance_doc = {
        "riderId": original["riderId"],
        "assetId": original["assetId"],
        "issuedAt": now,
        "reconciliationType": original["reconciliationType"],
        "totalCycles": original["totalCycles"],
        "cyclesCompleted": 0,
        "costPriceSnapshot": asset["costPrice"],
        "absorptionTypeSnapshot": asset["absorptionType"],
        "vendorIdSnapshot": asset.get("vendorId"),
        "deductionPerCycle": deduction_per_cycle,
        "totalRecovered": 0.0,
        "outstandingBalance": float(asset["costPrice"]),
        "status": "active",
        "replacementCount": original.get("replacementCount", 0) + 1,
        "replacementOf": oid,
        "createdAt": now,
    }

    if new_issuance_doc["vendorIdSnapshot"] and isinstance(
        new_issuance_doc["vendorIdSnapshot"], str
    ):
        new_issuance_doc["vendorIdSnapshot"] = ObjectId(new_issuance_doc["vendorIdSnapshot"])

    result = await db.asset_issuances.insert_one(new_issuance_doc)

    # Decrement stock for the replacement
    await db.asset_master.update_one(
        {"_id": original["assetId"]},
        {"$inc": {"stock": -1}, "$set": {"updatedAt": now}},
    )

    await audit.log(
        entity_type="replacement",
        entity_id=str(result.inserted_id),
        action="replaced",
        performed_by="LMD",
        performed_by_user_id=performed_by_user_id,
        before_value={"originalIssuanceId": issuance_id},
        after_value={"newIssuanceId": str(result.inserted_id), "midBalanceAction": mid_balance_action},
        reason=f"Asset replaced. Mid-balance action: {mid_balance_action}",
    )

    new_issuance = await db.asset_issuances.find_one({"_id": result.inserted_id})
    return {
        "originalIssuanceId": issuance_id,
        "newIssuanceId": str(result.inserted_id),
        "midBalanceAction": mid_balance_action,
        "newIssuance": _serialize_issuance(new_issuance),
    }
