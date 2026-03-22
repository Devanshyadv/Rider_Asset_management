from __future__ import annotations

import re
from typing import Any, Dict, List

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from config import get_db
from services.audit_service import AuditService
from services.settlement_engine import SettlementEngine
from services.vendor_service import VendorService

router = APIRouter(prefix="/api/reports", tags=["Reports"])


@router.get("/settlement/{week}")
async def full_settlement_report(
    week: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Full weekly settlement report for a given week cycle (e.g. '2026-W12').

    Returns ledger entries grouped by status, per-rider breakdowns,
    and vendor liability summary.
    """
    if not re.match(r"^\d{4}-W\d{1,2}$", week):
        raise HTTPException(
            status_code=400,
            detail="Invalid week format. Use YYYY-WNN (e.g. '2026-W12')",
        )

    engine = SettlementEngine(db)
    status_summary = await engine.get_settlement_status(week)

    # Per-rider breakdown
    pipeline = [
        {"$match": {"weekCycle": week}},
        {
            "$group": {
                "_id": "$riderId",
                "totalDue": {"$sum": "$amountDue"},
                "totalDeducted": {"$sum": "$amountDeducted"},
                "totalShortfall": {"$sum": "$shortfall"},
                "entries": {"$sum": 1},
            }
        },
        {"$sort": {"totalDeducted": -1}},
        {"$limit": 100},
    ]

    rider_breakdown: List[Dict[str, Any]] = []
    async for doc in db.asset_deduction_ledger.aggregate(pipeline):
        doc["riderId"] = str(doc.pop("_id"))
        rider_breakdown.append(doc)

    # Carry-forward summary
    cf_pipeline = [
        {"$match": {"weekCycle": week}},
        {
            "$group": {
                "_id": "$status",
                "count": {"$sum": 1},
                "totalCarryForward": {"$sum": "$outstandingCarryForward"},
            }
        },
    ]
    cf_summary: Dict[str, Any] = {}
    async for doc in db.rider_carry_forwards.aggregate(cf_pipeline):
        cf_summary[doc["_id"]] = {
            "count": doc["count"],
            "totalCarryForward": doc["totalCarryForward"],
        }

    return {
        "weekCycle": week,
        "summary": status_summary,
        "riderBreakdown": rider_breakdown,
        "carryForwardSummary": cf_summary,
    }


@router.get("/vendor-liability")
async def vendor_liability_report(
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Summary of all vendor liabilities across all vendors.
    """
    service = VendorService(db)
    summary = await service.get_vendor_liability_summary()

    # Total aggregates
    total_outstanding = sum(v.get("totalOutstanding", 0) for v in summary)
    total_recovered = sum(v.get("totalRecovered", 0) for v in summary)
    net_outstanding = sum(v.get("netOutstanding", 0) for v in summary)

    return {
        "vendors": summary,
        "totals": {
            "totalOutstanding": round(total_outstanding, 2),
            "totalRecovered": round(total_recovered, 2),
            "netOutstanding": round(net_outstanding, 2),
        },
    }


@router.get("/audit/{entity_id}")
async def audit_trail(
    entity_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Audit trail for any entity (issuance, deduction, vendor_liability, etc.)
    Accepts any valid entity ID string.
    """
    audit = AuditService(db)
    logs = await audit.get_audit_trail(entity_id)
    return {
        "entityId": entity_id,
        "totalEntries": len(logs),
        "logs": logs,
    }
