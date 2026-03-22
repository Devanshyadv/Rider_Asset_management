"""
Tests for issuance validation rules.

Covers:
8.  Issuance validation: stock=0 rejection (hard block).
9.  Issuance validation: cycles>4 rejection (hard block).
10. Duplicate issuance warning (same rider+asset, no confirm flag).
11. Duplicate issuance proceeds with confirm=True.
12. Asset inactive → hard block.
13. Asset not found → 404.
14. FLAT reconciliation with totalCycles != 1 → validation error.
15. BulkIssuanceRow validates cycles range.
16. BulkIssuanceRow normalises reconciliationType to uppercase.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest
from bson import ObjectId
from fastapi import HTTPException
from pydantic import ValidationError

from models.issuance import AssetIssuanceCreate, BulkIssuanceRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_oid() -> ObjectId:
    return ObjectId()


def _cursor(items: list):
    class _Cursor:
        def __init__(self, data):
            self._data = data

        def sort(self, *args, **kwargs):
            return self

        def __aiter__(self):
            return _async_iter(self._data).__aiter__()

    return _Cursor(items)


def _async_iter(items: list):
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


def build_mock_db(asset: Dict[str, Any] | None = None) -> MagicMock:
    db = MagicMock()
    db.asset_master = MagicMock()
    db.asset_master.find_one = AsyncMock(return_value=asset)
    db.asset_master.update_one = AsyncMock()

    db.asset_issuances = MagicMock()
    db.asset_issuances.find_one = AsyncMock(return_value=None)  # no duplicates by default
    db.asset_issuances.insert_one = AsyncMock(return_value=MagicMock(inserted_id=make_oid()))
    db.asset_issuances.find = MagicMock(return_value=_cursor([]))

    db.asset_audit_logs = MagicMock()
    db.asset_audit_logs.insert_one = AsyncMock(return_value=MagicMock(inserted_id=make_oid()))

    return db


def make_asset(
    stock: int = 10,
    status: str = "active",
    absorption: str = "SELF",
    vendor_id: ObjectId | None = None,
    cost_price: float = 1000.0,
) -> Dict[str, Any]:
    return {
        "_id": make_oid(),
        "name": "Test Asset",
        "costPrice": cost_price,
        "absorptionType": absorption,
        "vendorId": vendor_id,
        "status": status,
        "stock": stock,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# Import the function under test
# ---------------------------------------------------------------------------

from routes.issuances import _do_issue_asset


# ===========================================================================
# 8. stock = 0 rejection
# ===========================================================================

@pytest.mark.asyncio
async def test_issuance_rejected_when_stock_zero():
    """Hard block: asset with stock=0 cannot be issued."""
    asset = make_asset(stock=0)
    db = build_mock_db(asset=asset)

    rider_id = str(make_oid())
    asset_id = str(asset["_id"])

    payload = AssetIssuanceCreate(
        riderId=rider_id,
        assetId=asset_id,
        reconciliationType="EMI",
        totalCycles=2,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _do_issue_asset(db, payload, confirm=False)

    assert exc_info.value.status_code == 400
    assert "out of stock" in exc_info.value.detail.lower()


# ===========================================================================
# 9. cycles > 4 rejection (hard cap)
# ===========================================================================

def test_issuance_model_rejects_cycles_over_4():
    """Pydantic model rejects totalCycles > 4 before even hitting the DB."""
    with pytest.raises(ValidationError) as exc_info:
        AssetIssuanceCreate(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=5,
        )
    errors = exc_info.value.errors()
    assert any("4" in str(e) or "le" in str(e).lower() for e in errors)


@pytest.mark.asyncio
async def test_issuance_endpoint_rejects_cycles_over_4():
    """Even if model passes, _do_issue_asset hard-blocks cycles > 4."""
    asset = make_asset(stock=5)
    db = build_mock_db(asset=asset)

    rider_id = str(make_oid())
    asset_id = str(asset["_id"])

    # Construct payload manually bypassing model validation
    payload = AssetIssuanceCreate.__new__(AssetIssuanceCreate)
    object.__setattr__(payload, "riderId", rider_id)
    object.__setattr__(payload, "assetId", asset_id)
    object.__setattr__(payload, "reconciliationType", "EMI")
    object.__setattr__(payload, "totalCycles", 5)

    with pytest.raises(HTTPException) as exc_info:
        await _do_issue_asset(db, payload, confirm=False)

    assert exc_info.value.status_code == 400
    assert "4" in exc_info.value.detail


# ===========================================================================
# 10. Duplicate issuance warning (same rider + asset, no confirm)
# ===========================================================================

@pytest.mark.asyncio
async def test_duplicate_issuance_returns_warning_without_confirm():
    """Duplicate issuance returns a warning dict when confirm=False."""
    existing_id = make_oid()
    existing_issuance = {
        "_id": existing_id,
        "riderId": make_oid(),
        "assetId": make_oid(),
        "status": "active",
    }
    asset = make_asset(stock=5)
    db = build_mock_db(asset=asset)
    # Return an existing active issuance for the same rider+asset
    db.asset_issuances.find_one = AsyncMock(return_value=existing_issuance)

    payload = AssetIssuanceCreate(
        riderId=str(make_oid()),
        assetId=str(asset["_id"]),
        reconciliationType="EMI",
        totalCycles=2,
    )

    result = await _do_issue_asset(db, payload, confirm=False)

    assert "warning" in result
    assert result["existingIssuanceId"] == str(existing_id)
    # Should NOT have created a new issuance
    db.asset_issuances.insert_one.assert_not_called()


# ===========================================================================
# 11. Duplicate issuance proceeds with confirm=True
# ===========================================================================

@pytest.mark.asyncio
async def test_duplicate_issuance_proceeds_with_confirm():
    """Duplicate issuance proceeds and creates new record when confirm=True."""
    existing_issuance = {
        "_id": make_oid(),
        "riderId": make_oid(),
        "assetId": make_oid(),
        "status": "active",
    }
    new_id = make_oid()
    asset = make_asset(stock=5)

    # After insert, find_one should return a new issuance-like doc
    new_issuance_doc = {
        "_id": new_id,
        "riderId": make_oid(),
        "assetId": asset["_id"],
        "issuedAt": datetime.now(timezone.utc),
        "reconciliationType": "EMI",
        "totalCycles": 2,
        "cyclesCompleted": 0,
        "costPriceSnapshot": 1000.0,
        "absorptionTypeSnapshot": "SELF",
        "vendorIdSnapshot": None,
        "deductionPerCycle": 500.0,
        "totalRecovered": 0.0,
        "outstandingBalance": 1000.0,
        "status": "active",
        "replacementCount": 0,
        "replacementOf": None,
        "createdAt": datetime.now(timezone.utc),
    }

    db = build_mock_db(asset=asset)
    db.asset_issuances.find_one = AsyncMock(
        side_effect=[existing_issuance, new_issuance_doc]
    )
    db.asset_issuances.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id=new_id)
    )

    payload = AssetIssuanceCreate(
        riderId=str(make_oid()),
        assetId=str(asset["_id"]),
        reconciliationType="EMI",
        totalCycles=2,
    )

    result = await _do_issue_asset(db, payload, confirm=True)

    # Should NOT contain a warning key
    assert "warning" not in result
    db.asset_issuances.insert_one.assert_called_once()


# ===========================================================================
# 12. Asset inactive → hard block
# ===========================================================================

@pytest.mark.asyncio
async def test_issuance_rejected_when_asset_inactive():
    """Hard block: inactive asset cannot be issued."""
    asset = make_asset(stock=10, status="inactive")
    db = build_mock_db(asset=asset)

    payload = AssetIssuanceCreate(
        riderId=str(make_oid()),
        assetId=str(asset["_id"]),
        reconciliationType="EMI",
        totalCycles=2,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _do_issue_asset(db, payload, confirm=False)

    assert exc_info.value.status_code == 400
    assert "not active" in exc_info.value.detail.lower()


# ===========================================================================
# 13. Asset not found → 404
# ===========================================================================

@pytest.mark.asyncio
async def test_issuance_rejected_when_asset_not_found():
    """Asset not in DB → 404."""
    db = build_mock_db(asset=None)

    payload = AssetIssuanceCreate(
        riderId=str(make_oid()),
        assetId=str(make_oid()),
        reconciliationType="EMI",
        totalCycles=2,
    )

    with pytest.raises(HTTPException) as exc_info:
        await _do_issue_asset(db, payload, confirm=False)

    assert exc_info.value.status_code == 404


# ===========================================================================
# 14. FLAT reconciliation must have totalCycles = 1
# ===========================================================================

def test_flat_reconciliation_requires_single_cycle():
    """FLAT type with totalCycles != 1 should fail validation."""
    with pytest.raises(ValidationError):
        AssetIssuanceCreate(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="FLAT",
            totalCycles=2,
        )


def test_flat_reconciliation_with_single_cycle_passes():
    """FLAT type with totalCycles=1 is valid."""
    payload = AssetIssuanceCreate(
        riderId=str(make_oid()),
        assetId=str(make_oid()),
        reconciliationType="FLAT",
        totalCycles=1,
    )
    assert payload.totalCycles == 1
    assert payload.reconciliationType == "FLAT"


# ===========================================================================
# 15. BulkIssuanceRow validates cycles range
# ===========================================================================

def test_bulk_row_rejects_cycles_over_4():
    with pytest.raises(ValidationError):
        BulkIssuanceRow(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=5,
        )


def test_bulk_row_rejects_cycles_zero():
    with pytest.raises(ValidationError):
        BulkIssuanceRow(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=0,
        )


def test_bulk_row_accepts_valid_cycles():
    for cycles in (1, 2, 3, 4):
        row = BulkIssuanceRow(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=cycles,
        )
        assert row.totalCycles == cycles


# ===========================================================================
# 16. BulkIssuanceRow normalises reconciliationType to uppercase
# ===========================================================================

def test_bulk_row_normalises_recon_type():
    row = BulkIssuanceRow(
        riderId=str(make_oid()),
        assetId=str(make_oid()),
        reconciliationType="emi",
        totalCycles=2,
    )
    assert row.reconciliationType == "EMI"


def test_bulk_row_rejects_invalid_recon_type():
    with pytest.raises(ValidationError):
        BulkIssuanceRow(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="WEEKLY",
            totalCycles=2,
        )


# ===========================================================================
# AssetIssuanceCreate model validation
# ===========================================================================

class TestAssetIssuanceCreateModel:
    def test_valid_emi_payload(self):
        payload = AssetIssuanceCreate(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=4,
        )
        assert payload.reconciliationType == "EMI"
        assert payload.totalCycles == 4

    def test_invalid_objectid_rider(self):
        with pytest.raises(ValidationError):
            AssetIssuanceCreate(
                riderId="not-a-valid-id",
                assetId=str(make_oid()),
                reconciliationType="EMI",
                totalCycles=2,
            )

    def test_invalid_objectid_asset(self):
        with pytest.raises(ValidationError):
            AssetIssuanceCreate(
                riderId=str(make_oid()),
                assetId="bad-id",
                reconciliationType="EMI",
                totalCycles=2,
            )

    def test_invalid_reconciliation_type(self):
        with pytest.raises(ValidationError):
            AssetIssuanceCreate(
                riderId=str(make_oid()),
                assetId=str(make_oid()),
                reconciliationType="MONTHLY",
                totalCycles=2,
            )

    def test_min_cycles_boundary(self):
        payload = AssetIssuanceCreate(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=1,
        )
        assert payload.totalCycles == 1

    def test_max_cycles_boundary(self):
        payload = AssetIssuanceCreate(
            riderId=str(make_oid()),
            assetId=str(make_oid()),
            reconciliationType="EMI",
            totalCycles=4,
        )
        assert payload.totalCycles == 4
