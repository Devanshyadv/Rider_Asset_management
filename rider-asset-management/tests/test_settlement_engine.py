"""
Tests for the SettlementEngine and DeductionCalculator.

Covers:
1.  Rider earns exactly the deduction amount.
2.  Rider earns less than the deduction (partial deduction).
3.  Rider earns zero but is active (earns 0 this week).
4.  Rider inactive with VENDOR asset → passes to vendor_liabilities.
5.  Rider inactive with SELF asset → carry forward persists.
6.  Rider returns after gap (SELF carry-forward recovery at P0 priority).
7.  Vendor payout < liability (carry forward to next cycle).
8.  DeductionCalculator.compute_deduction_per_cycle correctness.
9.  DeductionCalculator.apply_deductions payout floor = 0 assertion.
10. Priority ordering: SELF carry-forward before SELF issuance before VENDOR issuance.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest
from bson import ObjectId

from services.deduction_calculator import DeductionCalculator
from services.settlement_engine import SettlementEngine, _week_cycle


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_oid() -> ObjectId:
    return ObjectId()


def make_issuance(
    rider_id: ObjectId,
    asset_id: ObjectId,
    absorption: str = "SELF",
    vendor_id: ObjectId | None = None,
    outstanding: float = 1000.0,
    deduction_per_cycle: float = 250.0,
    total_cycles: int = 4,
    cycles_completed: int = 0,
    status: str = "active",
) -> Dict[str, Any]:
    return {
        "_id": make_oid(),
        "riderId": rider_id,
        "assetId": asset_id,
        "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "reconciliationType": "EMI",
        "totalCycles": total_cycles,
        "cyclesCompleted": cycles_completed,
        "costPriceSnapshot": 1000.0,
        "absorptionTypeSnapshot": absorption,
        "vendorIdSnapshot": vendor_id,
        "deductionPerCycle": deduction_per_cycle,
        "totalRecovered": 0.0,
        "outstandingBalance": outstanding,
        "status": status,
        "replacementCount": 0,
        "replacementOf": None,
        "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
    }


def make_carry_forward(
    rider_id: ObjectId,
    issuance_id: ObjectId,
    outstanding: float = 50.0,
    week_cycle: str = "2026-W10",
    status: str = "pending",
) -> Dict[str, Any]:
    return {
        "_id": make_oid(),
        "riderId": rider_id,
        "issuanceId": issuance_id,
        "outstandingCarryForward": outstanding,
        "weekCycle": week_cycle,
        "status": status,
        "createdAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),  # Added for sorting
    }


# ---------------------------------------------------------------------------
# Fixture: mock DB
# ---------------------------------------------------------------------------

def build_mock_db() -> MagicMock:
    """Return a MagicMock that mimics AsyncIOMotorDatabase."""
    db = MagicMock()

    # Default async methods that return nothing
    for collection_name in (
        "asset_issuances",
        "asset_deduction_ledger",
        "vendor_liabilities",
        "rider_carry_forwards",
        "asset_audit_logs",
        "orders",
    ):
        coll = MagicMock()
        coll.insert_one = AsyncMock(return_value=MagicMock(inserted_id=make_oid()))
        coll.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        coll.update_many = AsyncMock(return_value=MagicMock(modified_count=1))
        coll.count_documents = AsyncMock(return_value=0)
        coll.find_one = AsyncMock(return_value=None)
        coll.aggregate = MagicMock(return_value=_async_iter([]))
        coll.find = MagicMock(return_value=_cursor([]))
        setattr(db, collection_name, coll)

    return db


def _async_iter(items: list):
    """Return an async iterator over items."""

    class _AI:
        def __init__(self, data):
            self._data = iter(data)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._data)
            except StopIteration:
                raise StopAsyncIteration

    return _AI(items)


def _cursor(items: list):
    """Chainable mock cursor that iterates items."""

    class _Cursor:
        def __init__(self, data):
            self._data = data

        def sort(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

        def __aiter__(self):
            return _async_iter(self._data).__aiter__()

    return _Cursor(items)


WEEK_START = datetime(2026, 3, 16, 0, 0, 0, tzinfo=timezone.utc)
WEEK_END = datetime(2026, 3, 22, 23, 59, 59, tzinfo=timezone.utc)
WEEK_CYCLE = "2026-W12"


# ===========================================================================
# 1. Rider earns exactly the deduction amount
# ===========================================================================

@pytest.mark.asyncio
async def test_exact_earnings_full_deduction():
    """Rider earns exactly 250 → full deduction, zero shortfall, payout=0."""
    rider_id = make_oid()
    asset_id = make_oid()
    issuance = make_issuance(rider_id, asset_id, deduction_per_cycle=250.0)

    db = build_mock_db()
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))
    db.orders.count_documents = AsyncMock(return_value=1)  # rider is active

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 250.0},
    )

    assert len(result["errors"]) == 0
    rider_result = result["riderResults"][0]
    assert rider_result["totalDeducted"] == 250.0
    assert rider_result["totalCarriedForward"] == 0.0
    assert rider_result["finalPayout"] == 0.0


# ===========================================================================
# 2. Rider earns less than deduction (partial deduction)
# ===========================================================================

@pytest.mark.asyncio
async def test_partial_earnings_carry_forward():
    """Rider earns 100 but owes 250 → deduct 100, carry forward 150."""
    rider_id = make_oid()
    asset_id = make_oid()
    issuance = make_issuance(rider_id, asset_id, deduction_per_cycle=250.0)

    db = build_mock_db()
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))
    db.rider_carry_forwards.find_one = AsyncMock(return_value=None)
    db.orders.count_documents = AsyncMock(return_value=1)  # active

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 100.0},
    )

    rider_result = result["riderResults"][0]
    assert rider_result["totalDeducted"] == 100.0
    assert rider_result["totalCarriedForward"] == 150.0
    assert rider_result["finalPayout"] == 0.0


# ===========================================================================
# 3. Rider earns zero but is active
# ===========================================================================

@pytest.mark.asyncio
async def test_active_rider_zero_earnings_no_deduction():
    """
    Rider is active (has orders) but earns 0 → no deduction, carry forward persists.
    """
    rider_id = make_oid()
    asset_id = make_oid()
    issuance = make_issuance(rider_id, asset_id, deduction_per_cycle=250.0)

    db = build_mock_db()
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))
    db.rider_carry_forwards.find_one = AsyncMock(return_value=None)
    db.orders.count_documents = AsyncMock(return_value=3)  # active, non-zero orders

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 0.0},
    )

    rider_result = result["riderResults"][0]
    # With 0 earnings: deducted=0, shortfall=250, carried_forward=True
    assert rider_result["totalDeducted"] == 0.0
    assert rider_result["finalPayout"] == 0.0


# ===========================================================================
# 4. Rider inactive with VENDOR asset → passes to vendor_liabilities
# ===========================================================================

@pytest.mark.asyncio
async def test_inactive_rider_vendor_asset_passed_to_vendor():
    """Rider has no orders this week + VENDOR issuance → vendor_liabilities created."""
    rider_id = make_oid()
    asset_id = make_oid()
    vendor_id = make_oid()
    issuance = make_issuance(
        rider_id, asset_id,
        absorption="VENDOR",
        vendor_id=vendor_id,
        outstanding=750.0,
    )

    db = build_mock_db()
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))
    db.orders.count_documents = AsyncMock(return_value=0)  # inactive

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 0.0},
    )

    # vendor_liabilities.insert_one should have been called
    db.vendor_liabilities.insert_one.assert_called_once()
    call_args = db.vendor_liabilities.insert_one.call_args[0][0]
    assert call_args["outstandingAmount"] == 750.0

    rider_result = result["riderResults"][0]
    assert rider_result["totalVendorPassed"] == 750.0
    assert rider_result["isActive"] is False


# ===========================================================================
# 5. Rider inactive with SELF asset → carry forward persists
# ===========================================================================

@pytest.mark.asyncio
async def test_inactive_rider_self_asset_carry_forward():
    """Rider has no orders this week + SELF issuance → carry forward is created/updated."""
    rider_id = make_oid()
    asset_id = make_oid()
    issuance = make_issuance(
        rider_id, asset_id,
        absorption="SELF",
        outstanding=500.0,
        deduction_per_cycle=250.0,
    )

    db = build_mock_db()
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))
    db.rider_carry_forwards.find_one = AsyncMock(return_value=None)
    db.orders.count_documents = AsyncMock(return_value=0)  # inactive

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 0.0},
    )

    # A skipped ledger entry should be persisted
    db.asset_deduction_ledger.insert_one.assert_called_once()
    ledger_call = db.asset_deduction_ledger.insert_one.call_args[0][0]
    assert ledger_call["status"] == "skipped"

    # Carry-forward should be created
    db.rider_carry_forwards.insert_one.assert_called_once()
    cf_call = db.rider_carry_forwards.insert_one.call_args[0][0]
    assert cf_call["outstandingCarryForward"] == 250.0
    assert cf_call["status"] == "pending"

    rider_result = result["riderResults"][0]
    assert rider_result["isActive"] is False


# ===========================================================================
# 6. Rider returns after gap (SELF carry-forward recovery at P0 priority)
# ===========================================================================

@pytest.mark.asyncio
async def test_returning_rider_carry_forward_p0_priority():
    """
    Rider has a SELF carry-forward from last week (P0) AND a new cycle due (P1).
    Earnings of 300 should satisfy P0 (50) first, then partially cover P1 (250).
    """
    rider_id = make_oid()
    asset_id = make_oid()
    issuance = make_issuance(rider_id, asset_id, absorption="SELF", deduction_per_cycle=250.0)

    carry_forward = make_carry_forward(
        rider_id,
        issuance["_id"],
        outstanding=50.0,
        week_cycle="2026-W11",
    )
    # Link issuance for the carry-forward lookup
    carry_forward["issuedAt"] = issuance["issuedAt"]

    db = build_mock_db()
    # Rider is now active
    db.orders.count_documents = AsyncMock(return_value=5)
    db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([{"_id": rider_id}]))

    # Setup: carry forwards return the CF, issuances return the issuance
    db.rider_carry_forwards.find = MagicMock(return_value=_cursor([carry_forward]))
    db.rider_carry_forwards.find_one = AsyncMock(return_value=None)

    # When looking up the issuance for the carry-forward, return the issuance
    db.asset_issuances.find_one = AsyncMock(return_value=issuance)
    db.asset_issuances.find = MagicMock(return_value=_cursor([issuance]))
    db.asset_issuances.update_one = AsyncMock()

    engine = SettlementEngine(db)
    result = await engine.run_weekly_settlement(
        week_start=WEEK_START,
        week_end=WEEK_END,
        rider_earnings={str(rider_id): 300.0},
    )

    rider_result = result["riderResults"][0]
    # P0: 50 deducted from carry-forward; P1: 250 deducted from issuance
    assert rider_result["totalDeducted"] == 300.0
    assert rider_result["finalPayout"] == 0.0


# ===========================================================================
# 7. Vendor payout < liability (carry forward to next cycle)
# ===========================================================================

@pytest.mark.asyncio
async def test_vendor_partial_settlement():
    """VendorService: partial payment should mark status as partially_recovered."""
    from services.vendor_service import VendorService

    vendor_id = make_oid()
    issuance_id = make_oid()
    rider_id = make_oid()
    liability_id = make_oid()

    liability = {
        "_id": liability_id,
        "vendorId": vendor_id,
        "issuanceId": issuance_id,
        "riderId": rider_id,
        "outstandingAmount": 500.0,
        "amountRecovered": 0.0,
        "status": "pending",
        "passedAt": datetime.now(timezone.utc),
        "weekCycle": "2026-W12",
        "createdAt": datetime.now(timezone.utc),
    }

    db = build_mock_db()
    db.vendor_liabilities.find = MagicMock(return_value=_cursor([liability]))
    db.vendor_liabilities.find_one = AsyncMock(return_value=liability)
    db.vendor_liabilities.update_one = AsyncMock()

    service = VendorService(db)
    result = await service.settle_vendor(
        vendor_id_str=str(vendor_id),
        amount_paid=200.0,
        note="Partial settlement",
    )

    assert result["amountApplied"] == 200.0
    assert result["amountUnused"] == 0.0
    assert len(result["liabilitiesSettled"]) == 1
    assert result["liabilitiesSettled"][0]["newStatus"] == "partially_recovered"

    # Check update was called with partially_recovered
    db.vendor_liabilities.update_one.assert_called_once()
    update_call = db.vendor_liabilities.update_one.call_args[0][1]["$set"]
    assert update_call["amountRecovered"] == 200.0
    assert update_call["status"] == "partially_recovered"


# ===========================================================================
# DeductionCalculator unit tests
# ===========================================================================

class TestDeductionCalculator:

    def test_compute_deduction_per_cycle_exact(self):
        """1000 / 4 = 250 exactly."""
        result = DeductionCalculator.compute_deduction_per_cycle(1000.0, 4)
        assert result == 250.0

    def test_compute_deduction_per_cycle_floor(self):
        """1000 / 3 = 333.33... → floor = 333."""
        result = DeductionCalculator.compute_deduction_per_cycle(1000.0, 3)
        assert result == 333.0

    def test_compute_deduction_per_cycle_single_cycle(self):
        """FLAT: 1000 / 1 = 1000."""
        result = DeductionCalculator.compute_deduction_per_cycle(1000.0, 1)
        assert result == 1000.0

    def test_compute_deduction_invalid_cycles(self):
        """Zero cycles should raise ValueError."""
        with pytest.raises(ValueError):
            DeductionCalculator.compute_deduction_per_cycle(1000.0, 0)

    def test_apply_deductions_payout_floor(self):
        """Final payout can never go below 0."""
        tasks = [
            {
                "taskType": "issuance",
                "absorptionTypeSnapshot": "SELF",
                "deductionPerCycle": 500.0,
                "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "_id": make_oid(),
            }
        ]
        results, final_payout = DeductionCalculator.apply_deductions(tasks, 300.0)
        assert final_payout >= 0
        assert final_payout == 0.0
        assert results[0]["amount_deducted"] == 300.0
        assert results[0]["shortfall"] == 200.0

    def test_apply_deductions_exact_match(self):
        """Earnings exactly equal deduction → status=deducted, payout=0."""
        tasks = [
            {
                "taskType": "issuance",
                "absorptionTypeSnapshot": "SELF",
                "deductionPerCycle": 250.0,
                "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "_id": make_oid(),
            }
        ]
        results, final_payout = DeductionCalculator.apply_deductions(tasks, 250.0)
        assert final_payout == 0.0
        assert results[0]["status"] == "deducted"
        assert results[0]["shortfall"] == 0.0
        assert results[0]["carried_forward"] is False

    def test_apply_deductions_surplus_payout(self):
        """Earnings > deduction → payout = surplus."""
        tasks = [
            {
                "taskType": "issuance",
                "absorptionTypeSnapshot": "SELF",
                "deductionPerCycle": 200.0,
                "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "_id": make_oid(),
            }
        ]
        results, final_payout = DeductionCalculator.apply_deductions(tasks, 500.0)
        assert final_payout == 300.0
        assert results[0]["amount_deducted"] == 200.0
        assert results[0]["status"] == "deducted"

    def test_sort_deductions_priority_order(self):
        """P0 carry-forwards come before P1a SELF, which come before P1b VENDOR."""
        cf = {"outstandingCarryForward": 50.0, "weekCycle": "2026-W10", "taskType": "carry_forward"}
        self_iss = {
            "absorptionTypeSnapshot": "SELF",
            "deductionPerCycle": 250.0,
            "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }
        vendor_iss = {
            "absorptionTypeSnapshot": "VENDOR",
            "deductionPerCycle": 300.0,
            "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }

        ordered = DeductionCalculator.sort_deductions([cf], [self_iss], [vendor_iss])
        assert len(ordered) == 3
        assert ordered[0]["priority"] == "P0"
        assert ordered[1]["priority"] == "P1a"
        assert ordered[2]["priority"] == "P1b"

    def test_apply_deductions_multiple_tasks_fifo(self):
        """Multiple tasks — deductions applied in given order until earnings exhausted."""
        tasks = [
            {
                "taskType": "carry_forward",
                "outstandingCarryForward": 50.0,
                "absorptionTypeSnapshot": "SELF",
                "weekCycle": "2026-W10",
                "_id": make_oid(),
            },
            {
                "taskType": "issuance",
                "absorptionTypeSnapshot": "SELF",
                "deductionPerCycle": 250.0,
                "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "_id": make_oid(),
            },
            {
                "taskType": "issuance",
                "absorptionTypeSnapshot": "VENDOR",
                "deductionPerCycle": 300.0,
                "issuedAt": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "_id": make_oid(),
            },
        ]
        # Only 200 available — should cover CF (50) + partial SELF (150)
        results, final_payout = DeductionCalculator.apply_deductions(tasks, 200.0)
        assert final_payout == 0.0
        assert results[0]["amount_deducted"] == 50.0   # CF fully recovered
        assert results[0]["status"] == "deducted"
        assert results[1]["amount_deducted"] == 150.0  # SELF partial
        assert results[1]["status"] == "partial"
        assert results[2]["amount_deducted"] == 0.0    # VENDOR skipped
        assert results[2]["status"] == "skipped"


# ===========================================================================
# week_cycle helper
# ===========================================================================

class TestWeekCycle:
    def test_week_cycle_format(self):
        dt = datetime(2026, 3, 22, tzinfo=timezone.utc)  # ISO week 12 of 2026
        wc = _week_cycle(dt)
        assert wc == "2026-W12"

    def test_week_cycle_leading_zero(self):
        dt = datetime(2026, 1, 5, tzinfo=timezone.utc)  # ISO week 2
        wc = _week_cycle(dt)
        assert wc == "2026-W02"
