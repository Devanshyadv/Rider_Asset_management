from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_db
from models.asset_master import AssetMasterCreate, AssetMasterUpdate
from services.audit_service import AuditService

router = APIRouter(prefix="/api/assets", tags=["Asset Master"])


def _serialize_asset(doc: Dict[str, Any]) -> Dict[str, Any]:
    doc["_id"] = str(doc["_id"])
    if doc.get("vendorId"):
        doc["vendorId"] = str(doc["vendorId"])
    return doc


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_asset(
    payload: AssetMasterCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new asset in the asset_master collection."""
    now = datetime.now(timezone.utc)
    doc = payload.model_dump()

    if doc.get("vendorId"):
        doc["vendorId"] = ObjectId(doc["vendorId"])

    doc["status"] = "active"
    doc["createdAt"] = now
    doc["updatedAt"] = now

    result = await db.asset_master.insert_one(doc)
    created = await db.asset_master.find_one({"_id": result.inserted_id})

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=str(result.inserted_id),
        action="issued",
        performed_by="Finance",
        after_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in created.items()},
        reason="Asset created",
    )

    return _serialize_asset(created)


@router.get("", response_model=List[Dict[str, Any]])
async def list_assets(
    status: Optional[str] = None,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List all assets, optionally filtered by status."""
    query: Dict[str, Any] = {}
    if status:
        if status not in ("active", "inactive"):
            raise HTTPException(
                status_code=400, detail="status must be 'active' or 'inactive'"
            )
        query["status"] = status

    assets = []
    async for doc in db.asset_master.find(query).sort("createdAt", -1):
        assets.append(_serialize_asset(doc))
    return assets


@router.patch("/{asset_id}")
async def update_asset(
    asset_id: str,
    payload: AssetMasterUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """Update stock, status, or pricing for an asset."""
    if not ObjectId.is_valid(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")

    oid = ObjectId(asset_id)
    existing = await db.asset_master.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Asset not found")

    update_data = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update")

    if "vendorId" in update_data and update_data["vendorId"]:
        update_data["vendorId"] = ObjectId(update_data["vendorId"])

    update_data["updatedAt"] = datetime.now(timezone.utc)

    await db.asset_master.update_one({"_id": oid}, {"$set": update_data})
    updated = await db.asset_master.find_one({"_id": oid})

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=asset_id,
        action="issued",
        performed_by="Finance",
        before_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in existing.items()},
        after_value={k: str(v) if isinstance(v, ObjectId) else v for k, v in updated.items()},
        reason="Asset updated",
    )

    return _serialize_asset(updated)


@router.delete("/{asset_id}")
async def soft_delete_asset(
    asset_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """Soft delete an asset by setting status = inactive."""
    if not ObjectId.is_valid(asset_id):
        raise HTTPException(status_code=400, detail="Invalid asset_id")

    oid = ObjectId(asset_id)
    existing = await db.asset_master.find_one({"_id": oid})
    if not existing:
        raise HTTPException(status_code=404, detail="Asset not found")

    if existing.get("status") == "inactive":
        raise HTTPException(status_code=400, detail="Asset is already inactive")

    now = datetime.now(timezone.utc)
    await db.asset_master.update_one(
        {"_id": oid}, {"$set": {"status": "inactive", "updatedAt": now}}
    )

    audit = AuditService(db)
    await audit.log(
        entity_type="issuance",
        entity_id=asset_id,
        action="written_off",
        performed_by="Finance",
        before_value={"status": existing.get("status")},
        after_value={"status": "inactive"},
        reason="Asset soft deleted",
    )

    return {"message": "Asset deactivated", "assetId": asset_id}
