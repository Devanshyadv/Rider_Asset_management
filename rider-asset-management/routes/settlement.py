from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import BaseModel, Field

from config import get_db
from services.settlement_engine import SettlementEngine

router = APIRouter(prefix="/api/settlement", tags=["Settlement"])


class WeeklySettlementRequest(BaseModel):
    weekStart: datetime = Field(
        ..., description="Week start datetime in ISO 8601 (UTC)"
    )
    weekEnd: datetime = Field(
        ..., description="Week end datetime in ISO 8601 (UTC)"
    )
    riderEarnings: Dict[str, float] = Field(
        ..., description="Map of riderId -> gross earnings for the week"
    )


@router.post("/run-weekly")
async def run_weekly_settlement(
    payload: WeeklySettlementRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Trigger the weekly settlement engine.

    Processes all riders with active issuances, applies deductions in
    priority order, and persists results to asset_deduction_ledger.
    """
    if payload.weekStart >= payload.weekEnd:
        raise HTTPException(
            status_code=400, detail="weekStart must be before weekEnd"
        )

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=payload.weekStart,
        week_end=payload.weekEnd,
        rider_earnings=payload.riderEarnings,
    )
    return result


@router.get("/status/{week}")
async def get_settlement_status(
    week: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> Dict[str, Any]:
    """
    Get settlement status for a given week cycle (e.g. '2026-W12').
    Returns a breakdown of ledger entries and vendor liabilities.
    """
    # Validate week format roughly
    import re
    if not re.match(r"^\d{4}-W\d{1,2}$", week):
        raise HTTPException(
            status_code=400,
            detail="Invalid week format. Use YYYY-WNN (e.g. '2026-W12')",
        )

    engine = SettlementEngine(db)
    return await engine.get_settlement_status(week)
