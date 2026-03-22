from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.audit_service import AuditService
from services.deduction_calculator import DeductionCalculator


def _week_cycle(dt: datetime) -> str:
    """Return ISO week cycle string e.g. '2026-W12'."""
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _serialize(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively convert ObjectId values to strings for audit logging."""
    if doc is None:
        return {}
    result = {}
    for k, v in doc.items():
        if isinstance(v, ObjectId):
            result[k] = str(v)
        elif isinstance(v, dict):
            result[k] = _serialize(v)
        elif isinstance(v, list):
            result[k] = [_serialize(i) if isinstance(i, dict) else (str(i) if isinstance(i, ObjectId) else i) for i in v]
        else:
            result[k] = v
    return result


class SettlementEngine:
    """
    Core weekly settlement engine.

    run_weekly_settlement() orchestrates all deductions for a given
    [week_start, week_end] window and persists results to MongoDB.
    """

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.audit = AuditService(db)
        self.calc = DeductionCalculator()

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def run_weekly_settlement(
        self,
        week_start: datetime,
        week_end: datetime,
        rider_earnings: Dict[str, float],  # {riderId_str: earnings_float}
    ) -> Dict[str, Any]:
        """
        Execute the full weekly settlement cycle.

        Args:
            week_start:     Start of the settlement week (UTC midnight).
            week_end:       End of the settlement week (UTC midnight).
            rider_earnings: Map of rider ObjectId strings → gross earnings.

        Returns a summary dict with processed/failed counts and per-rider results.
        """
        week_cycle = _week_cycle(week_start)
        summary: Dict[str, Any] = {
            "weekCycle": week_cycle,
            "weekStart": week_start.isoformat(),
            "weekEnd": week_end.isoformat(),
            "totalRidersProcessed": 0,
            "totalDeducted": 0.0,
            "totalCarriedForward": 0.0,
            "totalVendorPassed": 0.0,
            "riderResults": [],
            "errors": [],
        }

        # Gather all riders that have active issuances
        active_rider_ids = await self._get_riders_with_active_issuances()

        # Also gather riders from the earnings input so we don't miss anyone
        all_rider_ids = set(active_rider_ids) | set(rider_earnings.keys())

        for rider_id_str in all_rider_ids:
            try:
                result = await self._process_rider(
                    rider_id_str=rider_id_str,
                    week_start=week_start,
                    week_end=week_end,
                    week_cycle=week_cycle,
                    earnings=rider_earnings.get(rider_id_str, 0.0),
                )
                summary["riderResults"].append(result)
                summary["totalDeducted"] += result.get("totalDeducted", 0.0)
                summary["totalCarriedForward"] += result.get("totalCarriedForward", 0.0)
                summary["totalVendorPassed"] += result.get("totalVendorPassed", 0.0)
                summary["totalRidersProcessed"] += 1
            except Exception as exc:
                summary["errors"].append({"riderId": rider_id_str, "error": str(exc)})

        return summary

    # ------------------------------------------------------------------
    # Per-rider processing
    # ------------------------------------------------------------------

    async def _process_rider(
        self,
        rider_id_str: str,
        week_start: datetime,
        week_end: datetime,
        week_cycle: str,
        earnings: float,
    ) -> Dict[str, Any]:
        rider_oid = ObjectId(rider_id_str)
        is_active = earnings > 0 or await self._is_rider_active(rider_oid, week_start, week_end)

        rider_result: Dict[str, Any] = {
            "riderId": rider_id_str,
            "earnings": earnings,
            "isActive": is_active,
            "totalDeducted": 0.0,
            "totalCarriedForward": 0.0,
            "totalVendorPassed": 0.0,
            "finalPayout": earnings,
            "ledgerEntries": [],
        }

        if not is_active:
            # Handle inactive rider — no earnings, no deductions
            await self._handle_inactive_rider(
                rider_id_str=rider_id_str,
                week_start=week_start,
                week_end=week_end,
                week_cycle=week_cycle,
                rider_result=rider_result,
            )
            return rider_result

        # --- Active rider deduction flow ---

        # P0: pending SELF carry-forwards
        carry_forwards = await self._get_pending_carry_forwards(rider_oid)

        # P1: active issuances split by absorption type
        self_issuances, vendor_issuances = await self._get_active_issuances_split(rider_oid)

        # Sort into priority order
        ordered = self.calc.sort_deductions(carry_forwards, self_issuances, vendor_issuances)

        if not ordered:
            # Nothing to deduct
            rider_result["finalPayout"] = earnings
            return rider_result

        # Apply deductions
        results, final_payout = self.calc.apply_deductions(ordered, earnings)

        assert final_payout >= 0, f"Final payout negative for rider {rider_id_str}"

        rider_result["finalPayout"] = final_payout

        # Persist ledger entries and update issuances/carry-forwards
        for res in results:
            task = res["task"]
            task_type = task.get("taskType")

            if task_type == "carry_forward":
                await self._persist_carry_forward_recovery(
                    task=task,
                    res=res,
                    rider_id_str=rider_id_str,
                    week_start=week_start,
                    week_end=week_end,
                    week_cycle=week_cycle,
                )
            else:
                ledger_id = await self._persist_issuance_deduction(
                    task=task,
                    res=res,
                    rider_id_str=rider_id_str,
                    week_start=week_start,
                    week_end=week_end,
                    week_cycle=week_cycle,
                )
                rider_result["ledgerEntries"].append(ledger_id)

                if res["carried_forward"] and res["shortfall"] > 0:
                    await self._create_carry_forward(
                        issuance=task,
                        rider_id_str=rider_id_str,
                        shortfall=res["shortfall"],
                        week_cycle=week_cycle,
                    )

            rider_result["totalDeducted"] += res["amount_deducted"]
            if res["carried_forward"]:
                rider_result["totalCarriedForward"] += res["shortfall"]

        return rider_result

    # ------------------------------------------------------------------
    # Inactive rider handling
    # ------------------------------------------------------------------

    async def _handle_inactive_rider(
        self,
        rider_id_str: str,
        week_start: datetime,
        week_end: datetime,
        week_cycle: str,
        rider_result: Dict[str, Any],
    ) -> None:
        rider_oid = ObjectId(rider_id_str)
        self_issuances, vendor_issuances = await self._get_active_issuances_split(rider_oid)

        # VENDOR assets → pass to vendor_liabilities
        for iss in vendor_issuances:
            await self._pass_to_vendor(
                issuance=iss,
                rider_id_str=rider_id_str,
                week_cycle=week_cycle,
            )
            rider_result["totalVendorPassed"] += iss.get("outstandingBalance", 0.0)

        # SELF assets → keep carry forward (do NOT deduct, do NOT write-off yet)
        for iss in self_issuances:
            # Persist a "skipped" ledger entry to record the fact
            await self._persist_skipped_ledger(
                issuance=iss,
                rider_id_str=rider_id_str,
                week_start=week_start,
                week_end=week_end,
                week_cycle=week_cycle,
            )
            # Ensure carry forward record exists / is updated
            await self._create_carry_forward(
                issuance=iss,
                rider_id_str=rider_id_str,
                shortfall=iss.get("deductionPerCycle", 0.0),
                week_cycle=week_cycle,
            )
            rider_result["totalCarriedForward"] += iss.get("deductionPerCycle", 0.0)

    # ------------------------------------------------------------------
    # DB helpers — reads
    # ------------------------------------------------------------------

    async def _get_riders_with_active_issuances(self) -> List[str]:
        pipeline = [
            {"$match": {"status": "active"}},
            {"$group": {"_id": "$riderId"}},
        ]
        result = []
        async for doc in self.db.asset_issuances.aggregate(pipeline):
            result.append(str(doc["_id"]))
        return result

    async def _is_rider_active(
        self, rider_oid: ObjectId, week_start: datetime, week_end: datetime
    ) -> bool:
        """
        Rider is active if they have at least one order in [week_start, week_end].
        Uses the existing `orders` collection.
        """
        count = await self.db.orders.count_documents(
            {
                "rider": rider_oid,
                "createdAt": {"$gte": week_start, "$lte": week_end},
            }
        )
        return count > 0

    async def _get_pending_carry_forwards(self, rider_oid: ObjectId) -> List[Dict[str, Any]]:
        carry_forwards = []
        cursor = self.db.rider_carry_forwards.find(
            {"riderId": rider_oid, "status": "pending"}
        ).sort("weekCycle", 1)
        async for doc in cursor:
            # Only carry forwards linked to SELF issuances qualify for P0
            issuance = await self.db.asset_issuances.find_one({"_id": doc["issuanceId"]})
            if issuance and issuance.get("absorptionTypeSnapshot") == "SELF":
                carry_forwards.append({**doc, "issuedAt": issuance.get("issuedAt")})
        return carry_forwards

    async def _get_active_issuances_split(
        self, rider_oid: ObjectId
    ) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        self_issuances: List[Dict[str, Any]] = []
        vendor_issuances: List[Dict[str, Any]] = []

        cursor = self.db.asset_issuances.find(
            {"riderId": rider_oid, "status": "active"}
        ).sort("issuedAt", 1)

        async for doc in cursor:
            if doc.get("absorptionTypeSnapshot") == "SELF":
                self_issuances.append(doc)
            else:
                vendor_issuances.append(doc)

        return self_issuances, vendor_issuances

    # ------------------------------------------------------------------
    # DB helpers — writes
    # ------------------------------------------------------------------

    async def _persist_issuance_deduction(
        self,
        task: Dict[str, Any],
        res: Dict[str, Any],
        rider_id_str: str,
        week_start: datetime,
        week_end: datetime,
        week_cycle: str,
    ) -> str:
        issuance_id = task["_id"]
        now = datetime.now(timezone.utc)

        ledger_doc = {
            "issuanceId": issuance_id,
            "riderId": ObjectId(rider_id_str),
            "weekStartDate": week_start,
            "weekEndDate": week_end,
            "weekCycle": week_cycle,
            "amountDue": res["amount_due"],
            "amountDeducted": res["amount_deducted"],
            "shortfall": res["shortfall"],
            "carriedForward": res["carried_forward"],
            "status": res["status"],
            "createdAt": now,
        }
        insert_result = await self.db.asset_deduction_ledger.insert_one(ledger_doc)
        ledger_id = insert_result.inserted_id

        # Update issuance — increment cyclesCompleted, totalRecovered, outstandingBalance
        before = dict(task)
        new_recovered = task.get("totalRecovered", 0.0) + res["amount_deducted"]
        new_outstanding = max(0.0, task.get("outstandingBalance", 0.0) - res["amount_deducted"])
        new_cycles = task.get("cyclesCompleted", 0)

        # A cycle is "completed" only when fully deducted
        is_full_deduction = res["status"] == "deducted"
        if is_full_deduction:
            new_cycles += 1

        update_fields: Dict[str, Any] = {
            "totalRecovered": new_recovered,
            "outstandingBalance": new_outstanding,
            "cyclesCompleted": new_cycles,
            "updatedAt": now,
        }

        # Mark completed if all cycles done
        total_cycles = task.get("totalCycles", 1)
        if new_cycles >= total_cycles or new_outstanding <= 0:
            update_fields["status"] = "completed"

        await self.db.asset_issuances.update_one(
            {"_id": issuance_id}, {"$set": update_fields}
        )

        after = {**before, **update_fields}

        await self.audit.log(
            entity_type="deduction",
            entity_id=str(ledger_id),
            action="deducted",
            performed_by="system",
            before_value=_serialize(before),
            after_value=_serialize(after),
            reason=f"Weekly settlement {week_cycle}",
        )

        return str(ledger_id)

    async def _persist_carry_forward_recovery(
        self,
        task: Dict[str, Any],
        res: Dict[str, Any],
        rider_id_str: str,
        week_start: datetime,
        week_end: datetime,
        week_cycle: str,
    ) -> None:
        cf_id = task["_id"]
        now = datetime.now(timezone.utc)

        if res["status"] == "deducted":
            # Fully recovered
            await self.db.rider_carry_forwards.update_one(
                {"_id": cf_id},
                {"$set": {"status": "recovered", "updatedAt": now}},
            )
        else:
            # Partially recovered — reduce outstanding
            new_outstanding = res["shortfall"]
            await self.db.rider_carry_forwards.update_one(
                {"_id": cf_id},
                {
                    "$set": {
                        "outstandingCarryForward": new_outstanding,
                        "updatedAt": now,
                    }
                },
            )

        await self.audit.log(
            entity_type="deduction",
            entity_id=str(cf_id),
            action="carry_forward",
            performed_by="system",
            before_value=_serialize(task),
            after_value={"status": "recovered" if res["status"] == "deducted" else "partial", "amountDeducted": res["amount_deducted"]},
            reason=f"Carry-forward recovery for week {week_cycle}",
        )

    async def _persist_skipped_ledger(
        self,
        issuance: Dict[str, Any],
        rider_id_str: str,
        week_start: datetime,
        week_end: datetime,
        week_cycle: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        ledger_doc = {
            "issuanceId": issuance["_id"],
            "riderId": ObjectId(rider_id_str),
            "weekStartDate": week_start,
            "weekEndDate": week_end,
            "weekCycle": week_cycle,
            "amountDue": issuance.get("deductionPerCycle", 0.0),
            "amountDeducted": 0.0,
            "shortfall": issuance.get("deductionPerCycle", 0.0),
            "carriedForward": True,
            "status": "skipped",
            "createdAt": now,
        }
        await self.db.asset_deduction_ledger.insert_one(ledger_doc)

    async def _create_carry_forward(
        self,
        issuance: Dict[str, Any],
        rider_id_str: str,
        shortfall: float,
        week_cycle: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        # Check if a carry-forward record already exists for this issuance+week
        existing = await self.db.rider_carry_forwards.find_one(
            {
                "issuanceId": issuance["_id"],
                "riderId": ObjectId(rider_id_str),
                "weekCycle": week_cycle,
            }
        )
        if existing:
            await self.db.rider_carry_forwards.update_one(
                {"_id": existing["_id"]},
                {
                    "$set": {
                        "outstandingCarryForward": existing.get("outstandingCarryForward", 0.0) + shortfall,
                        "updatedAt": now,
                    }
                },
            )
        else:
            await self.db.rider_carry_forwards.insert_one(
                {
                    "riderId": ObjectId(rider_id_str),
                    "issuanceId": issuance["_id"],
                    "outstandingCarryForward": shortfall,
                    "weekCycle": week_cycle,
                    "status": "pending",
                    "createdAt": now,
                }
            )

        await self.audit.log(
            entity_type="deduction",
            entity_id=str(issuance["_id"]),
            action="carry_forward",
            performed_by="system",
            before_value={"outstandingBalance": issuance.get("outstandingBalance")},
            after_value={"shortfall": shortfall, "weekCycle": week_cycle},
            reason=f"Shortfall carried forward for week {week_cycle}",
        )

    async def _pass_to_vendor(
        self,
        issuance: Dict[str, Any],
        rider_id_str: str,
        week_cycle: str,
    ) -> None:
        now = datetime.now(timezone.utc)
        outstanding = issuance.get("outstandingBalance", 0.0)
        vendor_id = issuance.get("vendorIdSnapshot")

        if not vendor_id:
            return  # Nothing to do without a vendor

        liability_doc = {
            "vendorId": vendor_id,
            "issuanceId": issuance["_id"],
            "riderId": ObjectId(rider_id_str),
            "outstandingAmount": outstanding,
            "amountRecovered": 0.0,
            "status": "pending",
            "passedAt": now,
            "weekCycle": week_cycle,
            "createdAt": now,
        }
        result = await self.db.vendor_liabilities.insert_one(liability_doc)

        # Mark the issuance as vendor_passed
        await self.db.asset_issuances.update_one(
            {"_id": issuance["_id"]},
            {"$set": {"status": "vendor_passed", "updatedAt": now}},
        )

        await self.audit.log(
            entity_type="vendor_liability",
            entity_id=str(result.inserted_id),
            action="vendor_passed",
            performed_by="system",
            before_value=_serialize(issuance),
            after_value={"status": "vendor_passed", "weekCycle": week_cycle, "outstandingAmount": outstanding},
            reason=f"Rider inactive — VENDOR liability passed for week {week_cycle}",
        )

    # ------------------------------------------------------------------
    # Settlement status query
    # ------------------------------------------------------------------

    async def get_settlement_status(self, week: str) -> Dict[str, Any]:
        """Return aggregated settlement status for the given week cycle string."""
        pipeline = [
            {"$match": {"weekCycle": week}},
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "totalDeducted": {"$sum": "$amountDeducted"},
                    "totalDue": {"$sum": "$amountDue"},
                    "totalShortfall": {"$sum": "$shortfall"},
                }
            },
        ]
        status_breakdown: Dict[str, Any] = {}
        async for doc in self.db.asset_deduction_ledger.aggregate(pipeline):
            status_breakdown[doc["_id"]] = {
                "count": doc["count"],
                "totalDeducted": doc["totalDeducted"],
                "totalDue": doc["totalDue"],
                "totalShortfall": doc["totalShortfall"],
            }

        vendor_pipeline = [
            {"$match": {"weekCycle": week}},
            {
                "$group": {
                    "_id": None,
                    "count": {"$sum": 1},
                    "totalPassed": {"$sum": "$outstandingAmount"},
                }
            },
        ]
        vendor_summary = {"count": 0, "totalPassed": 0.0}
        async for doc in self.db.vendor_liabilities.aggregate(vendor_pipeline):
            vendor_summary = {"count": doc["count"], "totalPassed": doc["totalPassed"]}

        return {
            "weekCycle": week,
            "ledgerBreakdown": status_breakdown,
            "vendorLiabilities": vendor_summary,
        }
