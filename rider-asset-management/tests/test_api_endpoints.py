"""
tests/test_api_endpoints.py

Comprehensive API endpoint tests for:
    Fairdeal Rider Asset Management & Settlement System

Coverage
--------
  * Root             – GET /health, GET /
  * Asset Master     – POST/GET/PATCH/DELETE /api/assets
  * Issuances        – POST/GET /api/issuances, bulk upload, replace
  * Settlement       – POST run-weekly, GET status
  * Reports          – GET settlement, vendor-liability, audit
  * Vendors          – GET liabilities, POST settle
  * Admin            – waive, deduction-adjust, flag-abuse, year-end-writeoff
  * Rider            – GET /api/rider/{id}/assets

Run with:
    cd rider-asset-management
    pytest tests/test_api_endpoints.py -v
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

import main as main_module
from config import get_db


# ─────────────────────────────────────────────────────────────────────────────
# Utility helpers
# ─────────────────────────────────────────────────────────────────────────────

def oid() -> ObjectId:
    """Return a fresh ObjectId."""
    return ObjectId()


def sid() -> str:
    """Return a fresh ObjectId as string."""
    return str(ObjectId())


def _async_iter(items: list):
    """Async iterable over a plain list — used for .aggregate() mocks."""

    class _AI:
        def __init__(self, d):
            self._d = iter(d)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._d)
            except StopIteration:
                raise StopAsyncIteration

    return _AI(items)


def _cursor(items: list):
    """Chainable async cursor mock — used for .find() mocks."""

    class _C:
        def __init__(self, d):
            self._d = d

        def sort(self, *a, **kw):
            return self

        def limit(self, *a, **kw):
            return self

        def __aiter__(self):
            return _async_iter(self._d).__aiter__()

    return _C(items)


def build_mock_db() -> MagicMock:
    """Return a fully-wired mock of AsyncIOMotorDatabase."""
    db = MagicMock()
    for name in (
        "asset_master",
        "asset_issuances",
        "asset_deduction_ledger",
        "vendor_liabilities",
        "rider_carry_forwards",
        "asset_audit_logs",
        "orders",
    ):
        c = MagicMock()
        c.insert_one = AsyncMock(return_value=MagicMock(inserted_id=oid()))
        c.update_one = AsyncMock(return_value=MagicMock(modified_count=1))
        c.update_many = AsyncMock(return_value=MagicMock(modified_count=0))
        c.find_one = AsyncMock(return_value=None)
        c.count_documents = AsyncMock(return_value=0)
        c.find = MagicMock(return_value=_cursor([]))
        c.aggregate = MagicMock(return_value=_async_iter([]))
        setattr(db, name, c)
    return db


# ─────────────────────────────────────────────────────────────────────────────
# Document factory helpers
# ─────────────────────────────────────────────────────────────────────────────

def asset_doc(
    asset_id=None,
    status: str = "active",
    stock: int = 10,
    absorption: str = "SELF",
    vendor_id=None,
    cost: float = 1000.0,
) -> Dict[str, Any]:
    _id = asset_id or oid()
    return {
        "_id": _id,
        "name": "Test Asset",
        "costPrice": cost,
        "absorptionType": absorption,
        "vendorId": vendor_id,
        "status": status,
        "stock": stock,
        "createdAt": datetime.now(timezone.utc),
        "updatedAt": datetime.now(timezone.utc),
    }


def issuance_doc(
    issuance_id=None,
    rider_id=None,
    asset_id=None,
    status: str = "active",
    absorption: str = "SELF",
    vendor_id=None,
    outstanding: float = 1000.0,
    deduction: float = 250.0,
    cycles: int = 4,
    completed: int = 0,
    replacement_count: int = 0,
) -> Dict[str, Any]:
    _id = issuance_id or oid()
    return {
        "_id": _id,
        "riderId": rider_id or oid(),
        "assetId": asset_id or oid(),
        "issuedAt": datetime.now(timezone.utc),
        "reconciliationType": "EMI",
        "totalCycles": cycles,
        "cyclesCompleted": completed,
        "costPriceSnapshot": 1000.0,
        "absorptionTypeSnapshot": absorption,
        "vendorIdSnapshot": vendor_id,
        "deductionPerCycle": deduction,
        "totalRecovered": 0.0,
        "outstandingBalance": outstanding,
        "status": status,
        "replacementCount": replacement_count,
        "replacementOf": None,
        "createdAt": datetime.now(timezone.utc),
    }


def ledger_doc(
    ledger_id=None,
    issuance_id=None,
    rider_id=None,
) -> Dict[str, Any]:
    _id = ledger_id or oid()
    _iss_id = issuance_id or oid()
    _rider_id = rider_id or oid()
    return {
        "_id": _id,
        "issuanceId": _iss_id,
        "riderId": _rider_id,
        "weekStartDate": datetime(2026, 3, 16, tzinfo=timezone.utc),
        "weekEndDate": datetime(2026, 3, 22, tzinfo=timezone.utc),
        "weekCycle": "2026-W12",
        "amountDue": 250.0,
        "amountDeducted": 250.0,
        "shortfall": 0.0,
        "carriedForward": False,
        "status": "deducted",
        "createdAt": datetime.now(timezone.utc),
    }


def serialized_ledger_doc(
    ledger_id=None,
    issuance_id=None,
    rider_id=None,
) -> Dict[str, Any]:
    """Ledger doc with all ObjectId fields already stringified (for find_one returns after update)."""
    doc = ledger_doc(ledger_id, issuance_id, rider_id)
    doc["_id"] = str(doc["_id"])
    doc["issuanceId"] = str(doc["issuanceId"])
    doc["riderId"] = str(doc["riderId"])
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def mock_db() -> MagicMock:
    return build_mock_db()


@pytest_asyncio.fixture
async def client(mock_db: MagicMock):
    """Async HTTP client wired to the FastAPI app with a mocked DB."""
    main_module.app.dependency_overrides[get_db] = lambda: mock_db

    with patch.object(main_module, "connect_db", AsyncMock()), \
         patch.object(main_module, "close_db", AsyncMock()), \
         patch.object(main_module, "setup_cron_jobs", MagicMock()), \
         patch.object(main_module.scheduler, "start", MagicMock()), \
         patch.object(main_module.scheduler, "shutdown", MagicMock()):
        async with AsyncClient(
            transport=ASGITransport(app=main_module.app),
            base_url="http://test",
        ) as ac:
            yield ac

    main_module.app.dependency_overrides.clear()


# ══════════════════════════════════════════════════════════════════════════════
# TC-ROOT  Root Endpoints
# ══════════════════════════════════════════════════════════════════════════════

class TestRoot:

    async def test_root_001_health_check_returns_ok(self, client):
        """TC-ROOT-001: GET /health → 200 with status=ok."""
        r = await client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["service"] == "rider-asset-management"

    async def test_root_002_root_returns_service_info(self, client):
        """TC-ROOT-002: GET / → 200 with service metadata."""
        r = await client.get("/")
        assert r.status_code == 200
        data = r.json()
        assert "service" in data
        assert "version" in data
        assert data["docs"] == "/docs"


# ══════════════════════════════════════════════════════════════════════════════
# TC-ASSET  Asset Master
# ══════════════════════════════════════════════════════════════════════════════

class TestAssetCreate:

    async def test_asset_001_create_self_absorption(self, client, mock_db):
        """TC-ASSET-001: Create asset with SELF absorption → 201."""
        doc = asset_doc()
        mock_db.asset_master.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=doc["_id"])
        )
        mock_db.asset_master.find_one = AsyncMock(return_value=doc)

        r = await client.post("/api/assets", json={
            "name": "Delivery Bag",
            "costPrice": 1000.0,
            "absorptionType": "SELF",
            "stock": 50,
        })
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Test Asset"
        assert body["status"] == "active"

    async def test_asset_002_create_vendor_absorption(self, client, mock_db):
        """TC-ASSET-002: Create asset with VENDOR absorption + vendorId → 201."""
        vendor_id = oid()
        doc = asset_doc(absorption="VENDOR", vendor_id=vendor_id)
        mock_db.asset_master.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=doc["_id"])
        )
        mock_db.asset_master.find_one = AsyncMock(return_value=doc)

        r = await client.post("/api/assets", json={
            "name": "Helmet",
            "costPrice": 500.0,
            "absorptionType": "VENDOR",
            "vendorId": str(vendor_id),
            "stock": 20,
        })
        assert r.status_code == 201

    async def test_asset_003_vendor_absorption_missing_vendor_id(self, client, mock_db):
        """TC-ASSET-003: VENDOR absorptionType without vendorId → 422."""
        r = await client.post("/api/assets", json={
            "name": "Jacket",
            "costPrice": 300.0,
            "absorptionType": "VENDOR",
            "stock": 10,
        })
        assert r.status_code == 422

    async def test_asset_004_negative_cost_price(self, client, mock_db):
        """TC-ASSET-004: costPrice < 0 → 422."""
        r = await client.post("/api/assets", json={
            "name": "Bad Asset",
            "costPrice": -100.0,
            "absorptionType": "SELF",
            "stock": 5,
        })
        assert r.status_code == 422

    async def test_asset_005_zero_cost_price(self, client, mock_db):
        """TC-ASSET-005: costPrice = 0 (must be > 0) → 422."""
        r = await client.post("/api/assets", json={
            "name": "Zero Asset",
            "costPrice": 0.0,
            "absorptionType": "SELF",
            "stock": 5,
        })
        assert r.status_code == 422

    async def test_asset_006_invalid_absorption_type(self, client, mock_db):
        """TC-ASSET-006: absorptionType not VENDOR/SELF → 422."""
        r = await client.post("/api/assets", json={
            "name": "Asset",
            "costPrice": 500.0,
            "absorptionType": "SHARED",
            "stock": 10,
        })
        assert r.status_code == 422

    async def test_asset_007_zero_stock_is_valid(self, client, mock_db):
        """TC-ASSET-007: stock=0 is allowed (out of stock but record valid) → 201."""
        doc = asset_doc(stock=0)
        mock_db.asset_master.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=doc["_id"])
        )
        mock_db.asset_master.find_one = AsyncMock(return_value=doc)

        r = await client.post("/api/assets", json={
            "name": "Out of Stock Asset",
            "costPrice": 800.0,
            "absorptionType": "SELF",
            "stock": 0,
        })
        assert r.status_code == 201

    async def test_asset_008_empty_name(self, client, mock_db):
        """TC-ASSET-008: name="" (min_length=1) → 422."""
        r = await client.post("/api/assets", json={
            "name": "",
            "costPrice": 500.0,
            "absorptionType": "SELF",
            "stock": 10,
        })
        assert r.status_code == 422


class TestAssetList:

    async def test_asset_009_list_all_assets(self, client, mock_db):
        """TC-ASSET-009: GET /api/assets → 200 with all docs."""
        docs = [asset_doc() for _ in range(3)]
        mock_db.asset_master.find = MagicMock(return_value=_cursor(docs))

        r = await client.get("/api/assets")
        assert r.status_code == 200
        assert len(r.json()) == 3

    async def test_asset_010_list_filter_active(self, client, mock_db):
        """TC-ASSET-010: Filter status=active → 200."""
        docs = [asset_doc(status="active")]
        mock_db.asset_master.find = MagicMock(return_value=_cursor(docs))

        r = await client.get("/api/assets?status=active")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_asset_011_list_filter_inactive(self, client, mock_db):
        """TC-ASSET-011: Filter status=inactive → 200."""
        docs = [asset_doc(status="inactive")]
        mock_db.asset_master.find = MagicMock(return_value=_cursor(docs))

        r = await client.get("/api/assets?status=inactive")
        assert r.status_code == 200

    async def test_asset_012_list_invalid_status_filter(self, client, mock_db):
        """TC-ASSET-012: status=unknown → 400."""
        r = await client.get("/api/assets?status=unknown")
        assert r.status_code == 400

    async def test_asset_013_list_empty_collection(self, client, mock_db):
        """TC-ASSET-013: No assets → empty list."""
        mock_db.asset_master.find = MagicMock(return_value=_cursor([]))

        r = await client.get("/api/assets")
        assert r.status_code == 200
        assert r.json() == []


class TestAssetUpdate:

    async def test_asset_014_update_valid_fields(self, client, mock_db):
        """TC-ASSET-014: PATCH valid stock and costPrice → 200."""
        _id = oid()
        before = asset_doc(asset_id=_id)
        after = {**before, "stock": 99, "costPrice": 950.0, "updatedAt": datetime.now(timezone.utc)}
        mock_db.asset_master.find_one = AsyncMock(side_effect=[before, after])

        r = await client.patch(f"/api/assets/{_id}", json={"stock": 99, "costPrice": 950.0})
        assert r.status_code == 200

    async def test_asset_015_update_invalid_object_id(self, client, mock_db):
        """TC-ASSET-015: Invalid asset_id format → 400."""
        r = await client.patch("/api/assets/not-an-id", json={"stock": 5})
        assert r.status_code == 400

    async def test_asset_016_update_not_found(self, client, mock_db):
        """TC-ASSET-016: Asset not in DB → 404."""
        mock_db.asset_master.find_one = AsyncMock(return_value=None)

        r = await client.patch(f"/api/assets/{sid()}", json={"stock": 5})
        assert r.status_code == 404

    async def test_asset_017_update_no_fields(self, client, mock_db):
        """TC-ASSET-017: Empty body (no fields to update) → 400."""
        _id = oid()
        mock_db.asset_master.find_one = AsyncMock(return_value=asset_doc(asset_id=_id))

        r = await client.patch(f"/api/assets/{_id}", json={})
        assert r.status_code == 400


class TestAssetDelete:

    async def test_asset_018_delete_active_asset(self, client, mock_db):
        """TC-ASSET-018: Soft-delete active asset → 200 with 'Asset deactivated'."""
        _id = oid()
        mock_db.asset_master.find_one = AsyncMock(
            return_value=asset_doc(asset_id=_id, status="active")
        )

        r = await client.delete(f"/api/assets/{_id}")
        assert r.status_code == 200
        assert r.json()["message"] == "Asset deactivated"
        assert r.json()["assetId"] == str(_id)

    async def test_asset_019_delete_already_inactive(self, client, mock_db):
        """TC-ASSET-019: Asset already inactive → 400."""
        _id = oid()
        mock_db.asset_master.find_one = AsyncMock(
            return_value=asset_doc(asset_id=_id, status="inactive")
        )

        r = await client.delete(f"/api/assets/{_id}")
        assert r.status_code == 400

    async def test_asset_020_delete_invalid_id(self, client, mock_db):
        """TC-ASSET-020: Invalid ObjectId → 400."""
        r = await client.delete("/api/assets/bad-id")
        assert r.status_code == 400

    async def test_asset_021_delete_not_found(self, client, mock_db):
        """TC-ASSET-021: Asset not found → 404."""
        mock_db.asset_master.find_one = AsyncMock(return_value=None)

        r = await client.delete(f"/api/assets/{sid()}")
        assert r.status_code == 404


# ══════════════════════════════════════════════════════════════════════════════
# TC-ISS  Issuances
# ══════════════════════════════════════════════════════════════════════════════

class TestIssuanceCreate:

    async def test_iss_001_emi_4_cycles(self, client, mock_db):
        """TC-ISS-001: Issue EMI asset with 4 cycles → 201."""
        a = asset_doc()
        iss = issuance_doc(asset_id=a["_id"], cycles=4)
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[None, iss])
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=iss["_id"])
        )

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 4,
        })
        assert r.status_code == 201
        assert "deductionPerCycle" in r.json()

    async def test_iss_002_flat_1_cycle(self, client, mock_db):
        """TC-ISS-002: Issue FLAT asset with 1 cycle → 201."""
        a = asset_doc()
        iss = issuance_doc(asset_id=a["_id"], cycles=1)
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[None, iss])
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=iss["_id"])
        )

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "FLAT", "totalCycles": 1,
        })
        assert r.status_code == 201

    async def test_iss_003_asset_out_of_stock(self, client, mock_db):
        """TC-ISS-003: stock=0 hard block → 400 with 'out of stock'."""
        a = asset_doc(stock=0)
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 2,
        })
        assert r.status_code == 400
        assert "stock" in r.json()["detail"].lower()

    async def test_iss_004_inactive_asset(self, client, mock_db):
        """TC-ISS-004: Inactive asset hard block → 400 with 'not active'."""
        a = asset_doc(status="inactive")
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 2,
        })
        assert r.status_code == 400
        assert "not active" in r.json()["detail"].lower()

    async def test_iss_005_asset_not_found(self, client, mock_db):
        """TC-ISS-005: Asset not in DB → 404."""
        mock_db.asset_master.find_one = AsyncMock(return_value=None)

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": sid(),
            "reconciliationType": "EMI", "totalCycles": 2,
        })
        assert r.status_code == 404

    async def test_iss_006_cycles_over_4(self, client, mock_db):
        """TC-ISS-006: totalCycles > 4 → 422 (Pydantic hard cap)."""
        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": sid(),
            "reconciliationType": "EMI", "totalCycles": 5,
        })
        assert r.status_code == 422

    async def test_iss_007_cycles_zero(self, client, mock_db):
        """TC-ISS-007: totalCycles=0 (below minimum of 1) → 422."""
        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": sid(),
            "reconciliationType": "EMI", "totalCycles": 0,
        })
        assert r.status_code == 422

    async def test_iss_008_flat_with_cycles_not_1(self, client, mock_db):
        """TC-ISS-008: FLAT reconciliationType with totalCycles!=1 → 422."""
        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": sid(),
            "reconciliationType": "FLAT", "totalCycles": 2,
        })
        assert r.status_code == 422

    async def test_iss_009_invalid_rider_id(self, client, mock_db):
        """TC-ISS-009: riderId is not a valid ObjectId → 422."""
        r = await client.post("/api/issuances", json={
            "riderId": "not-a-valid-id", "assetId": sid(),
            "reconciliationType": "EMI", "totalCycles": 2,
        })
        assert r.status_code == 422

    async def test_iss_010_invalid_reconciliation_type(self, client, mock_db):
        """TC-ISS-010: reconciliationType='WEEKLY' (not FLAT/EMI) → 422."""
        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": sid(),
            "reconciliationType": "WEEKLY", "totalCycles": 2,
        })
        assert r.status_code == 422

    async def test_iss_011_duplicate_without_confirm(self, client, mock_db):
        """TC-ISS-011: Duplicate active issuance for same rider+asset without confirm → warning body."""
        a = asset_doc()
        existing = issuance_doc(asset_id=a["_id"])
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(return_value=existing)

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 4,
        })
        assert r.status_code == 201
        body = r.json()
        assert "warning" in body
        assert "existingIssuanceId" in body
        mock_db.asset_issuances.insert_one.assert_not_called()

    async def test_iss_012_duplicate_with_confirm(self, client, mock_db):
        """TC-ISS-012: Duplicate with confirm=true proceeds and creates new record."""
        a = asset_doc()
        existing = issuance_doc(asset_id=a["_id"])
        new_iss = issuance_doc(asset_id=a["_id"])
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[existing, new_iss])
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=new_iss["_id"])
        )

        r = await client.post("/api/issuances?confirm=true", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 4,
        })
        assert r.status_code == 201
        assert "warning" not in r.json()
        mock_db.asset_issuances.insert_one.assert_called_once()

    async def test_iss_013_emi_boundary_min_cycles(self, client, mock_db):
        """TC-ISS-013: totalCycles=1 with EMI → 201 (boundary)."""
        a = asset_doc()
        iss = issuance_doc(asset_id=a["_id"], cycles=1)
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[None, iss])
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=iss["_id"])
        )

        r = await client.post("/api/issuances", json={
            "riderId": sid(), "assetId": str(a["_id"]),
            "reconciliationType": "EMI", "totalCycles": 1,
        })
        assert r.status_code == 201


class TestIssuanceGet:

    async def test_iss_014_get_rider_issuances(self, client, mock_db):
        """TC-ISS-014: GET all issuances for a rider → 200 with list."""
        rider_id = oid()
        docs = [issuance_doc(rider_id=rider_id) for _ in range(2)]
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor(docs))

        r = await client.get(f"/api/issuances/{rider_id}")
        assert r.status_code == 200
        assert len(r.json()) == 2

    async def test_iss_015_get_issuances_invalid_rider_id(self, client, mock_db):
        """TC-ISS-015: Invalid rider_id ObjectId → 400."""
        r = await client.get("/api/issuances/not-valid-id")
        assert r.status_code == 400

    async def test_iss_016_get_issuances_filter_status(self, client, mock_db):
        """TC-ISS-016: Filter issuances by status=active → 200."""
        rider_id = oid()
        docs = [issuance_doc(rider_id=rider_id, status="active")]
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor(docs))

        r = await client.get(f"/api/issuances/{rider_id}?status=active")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_iss_017_get_issuances_empty(self, client, mock_db):
        """TC-ISS-017: Rider with no issuances → empty list."""
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([]))

        r = await client.get(f"/api/issuances/{sid()}")
        assert r.status_code == 200
        assert r.json() == []


class TestIssuanceBulk:

    async def test_iss_018_bulk_valid_csv(self, client, mock_db):
        """TC-ISS-018: Bulk upload valid CSV → 200 with processed count."""
        a = asset_doc()
        iss = issuance_doc(asset_id=a["_id"])
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(
            side_effect=[None, iss, None, iss]
        )
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=iss["_id"])
        )

        csv_content = (
            "riderId,assetId,reconciliationType,totalCycles\n"
            f"{sid()},{a['_id']},EMI,4\n"
            f"{sid()},{a['_id']},FLAT,1\n"
        )
        r = await client.post(
            "/api/issuances/bulk",
            files={"file": ("issuances.csv", csv_content.encode(), "text/csv")},
        )
        assert r.status_code == 200
        body = r.json()
        assert "processed" in body
        assert "rejected" in body
        assert body["processed"] == 2

    async def test_iss_019_bulk_missing_columns(self, client, mock_db):
        """TC-ISS-019: CSV missing required columns → 400."""
        csv_content = "name,type\nfoo,bar\n"
        r = await client.post(
            "/api/issuances/bulk",
            files={"file": ("bad.csv", csv_content.encode(), "text/csv")},
        )
        assert r.status_code == 400

    async def test_iss_020_bulk_partial_failures(self, client, mock_db):
        """TC-ISS-020: CSV with mix of valid and invalid rows → rejected list populated."""
        a = asset_doc()
        iss = issuance_doc(asset_id=a["_id"])
        mock_db.asset_master.find_one = AsyncMock(return_value=a)
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[None, iss])
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=iss["_id"])
        )

        csv_content = (
            "riderId,assetId,reconciliationType,totalCycles\n"
            f"{sid()},{a['_id']},EMI,4\n"
            f"INVALID-ID,{a['_id']},EMI,4\n"
        )
        r = await client.post(
            "/api/issuances/bulk",
            files={"file": ("mix.csv", csv_content.encode(), "text/csv")},
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["rejected"]) >= 1

    async def test_iss_021_bulk_all_invalid_rows(self, client, mock_db):
        """TC-ISS-021: All CSV rows invalid → processed=0, all rows rejected."""
        csv_content = (
            "riderId,assetId,reconciliationType,totalCycles\n"
            f"BAD-ID,BAD-ID,INVALID,99\n"
            f"BAD-ID2,BAD-ID2,INVALID,0\n"
        )
        r = await client.post(
            "/api/issuances/bulk",
            files={"file": ("all_bad.csv", csv_content.encode(), "text/csv")},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["processed"] == 0
        assert len(body["rejected"]) == 2


class TestIssuanceReplace:

    async def test_iss_022_replace_waive_remaining(self, client, mock_db):
        """TC-ISS-022: Replace issuance with waive_remaining → 201, midBalanceAction=waive_remaining."""
        a = asset_doc()
        orig = issuance_doc(asset_id=a["_id"], status="active", outstanding=500.0)
        new_iss = issuance_doc(asset_id=a["_id"], status="active")
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[orig, new_iss])
        mock_db.asset_issuances.count_documents = AsyncMock(return_value=0)
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=new_iss["_id"])
        )
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.post(
            f"/api/issuances/{orig['_id']}/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 201
        body = r.json()
        assert body["midBalanceAction"] == "waive_remaining"
        assert "newIssuanceId" in body

    async def test_iss_023_replace_pass_to_vendor(self, client, mock_db):
        """TC-ISS-023: Replace with pass_to_vendor → 201, vendor_liabilities insert called."""
        vendor_id = oid()
        a = asset_doc(absorption="VENDOR", vendor_id=vendor_id)
        orig = issuance_doc(
            asset_id=a["_id"], absorption="VENDOR",
            vendor_id=vendor_id, status="active", outstanding=750.0
        )
        new_iss = issuance_doc(asset_id=a["_id"], status="active")
        mock_db.asset_issuances.find_one = AsyncMock(side_effect=[orig, new_iss])
        mock_db.asset_issuances.count_documents = AsyncMock(return_value=0)
        mock_db.asset_issuances.insert_one = AsyncMock(
            return_value=MagicMock(inserted_id=new_iss["_id"])
        )
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.post(
            f"/api/issuances/{orig['_id']}/replace?mid_balance_action=pass_to_vendor"
        )
        assert r.status_code == 201
        assert r.json()["midBalanceAction"] == "pass_to_vendor"
        mock_db.vendor_liabilities.insert_one.assert_called_once()

    async def test_iss_024_replace_non_active_issuance(self, client, mock_db):
        """TC-ISS-024: Replacing a completed issuance → 400."""
        orig = issuance_doc(status="completed")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=orig)

        r = await client.post(
            f"/api/issuances/{orig['_id']}/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 400

    async def test_iss_025_replace_issuance_not_found(self, client, mock_db):
        """TC-ISS-025: Issuance not found → 404."""
        mock_db.asset_issuances.find_one = AsyncMock(return_value=None)

        r = await client.post(
            f"/api/issuances/{sid()}/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 404

    async def test_iss_026_replace_invalid_issuance_id(self, client, mock_db):
        """TC-ISS-026: Invalid issuance_id ObjectId → 400."""
        r = await client.post(
            "/api/issuances/bad-id/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 400

    async def test_iss_027_replace_invalid_mid_balance_action(self, client, mock_db):
        """TC-ISS-027: mid_balance_action not in allowed values → 422."""
        r = await client.post(
            f"/api/issuances/{sid()}/replace?mid_balance_action=unknown_action"
        )
        assert r.status_code == 422

    async def test_iss_028_replace_max_replacements_exceeded(self, client, mock_db):
        """TC-ISS-028: 2 existing replacements → 400 (max cap reached)."""
        orig = issuance_doc(status="active")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=orig)
        mock_db.asset_issuances.count_documents = AsyncMock(return_value=2)

        r = await client.post(
            f"/api/issuances/{orig['_id']}/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 400
        assert "replacement" in r.json()["detail"].lower()

    async def test_iss_029_replace_asset_out_of_stock(self, client, mock_db):
        """TC-ISS-029: Replacement asset has stock=0 → 400."""
        a = asset_doc(stock=0)
        orig = issuance_doc(asset_id=a["_id"], status="active")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=orig)
        mock_db.asset_issuances.count_documents = AsyncMock(return_value=0)
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.post(
            f"/api/issuances/{orig['_id']}/replace?mid_balance_action=waive_remaining"
        )
        assert r.status_code == 400
        assert "stock" in r.json()["detail"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# TC-SETT  Settlement
# ══════════════════════════════════════════════════════════════════════════════

class TestSettlement:

    async def test_sett_001_run_weekly_settlement(self, client, mock_db):
        """TC-SETT-001: POST run-weekly with valid payload → 200 with weekCycle."""
        rider_id = sid()
        mock_db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([]))
        mock_db.orders.count_documents = AsyncMock(return_value=1)
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([]))
        mock_db.rider_carry_forwards.find = MagicMock(return_value=_cursor([]))

        r = await client.post("/api/settlement/run-weekly", json={
            "weekStart": "2026-03-16T00:00:00Z",
            "weekEnd": "2026-03-22T23:59:59Z",
            "riderEarnings": {rider_id: 3500.0},
        })
        assert r.status_code == 200
        body = r.json()
        assert body["weekCycle"] == "2026-W12"
        assert "totalRidersProcessed" in body

    async def test_sett_002_week_start_after_end(self, client, mock_db):
        """TC-SETT-002: weekStart >= weekEnd → 400."""
        r = await client.post("/api/settlement/run-weekly", json={
            "weekStart": "2026-03-22T00:00:00Z",
            "weekEnd": "2026-03-16T00:00:00Z",
            "riderEarnings": {},
        })
        assert r.status_code == 400

    async def test_sett_003_equal_week_start_end(self, client, mock_db):
        """TC-SETT-003: weekStart == weekEnd → 400."""
        r = await client.post("/api/settlement/run-weekly", json={
            "weekStart": "2026-03-16T00:00:00Z",
            "weekEnd": "2026-03-16T00:00:00Z",
            "riderEarnings": {},
        })
        assert r.status_code == 400

    async def test_sett_004_empty_rider_earnings(self, client, mock_db):
        """TC-SETT-004: Empty riderEarnings → 200, totalRidersProcessed=0."""
        mock_db.asset_issuances.aggregate = MagicMock(return_value=_async_iter([]))

        r = await client.post("/api/settlement/run-weekly", json={
            "weekStart": "2026-03-16T00:00:00Z",
            "weekEnd": "2026-03-22T23:59:59Z",
            "riderEarnings": {},
        })
        assert r.status_code == 200
        assert r.json()["totalRidersProcessed"] == 0

    async def test_sett_005_get_status_valid_week(self, client, mock_db):
        """TC-SETT-005: GET /status/2026-W12 → 200 with weekCycle in body."""
        mock_db.asset_deduction_ledger.aggregate = MagicMock(return_value=_async_iter([]))
        mock_db.vendor_liabilities.aggregate = MagicMock(return_value=_async_iter([]))

        r = await client.get("/api/settlement/status/2026-W12")
        assert r.status_code == 200
        assert r.json()["weekCycle"] == "2026-W12"

    async def test_sett_006_get_status_invalid_week_format(self, client, mock_db):
        """TC-SETT-006: Invalid week format → 400."""
        r = await client.get("/api/settlement/status/week-twelve")
        assert r.status_code == 400

    async def test_sett_007_get_status_single_digit_week(self, client, mock_db):
        """TC-SETT-007: Single-digit week '2026-W5' is valid → 200."""
        mock_db.asset_deduction_ledger.aggregate = MagicMock(return_value=_async_iter([]))
        mock_db.vendor_liabilities.aggregate = MagicMock(return_value=_async_iter([]))

        r = await client.get("/api/settlement/status/2026-W5")
        assert r.status_code == 200


# ══════════════════════════════════════════════════════════════════════════════
# TC-REP  Reports
# ══════════════════════════════════════════════════════════════════════════════

class TestReports:

    async def test_rep_001_settlement_report_valid(self, client, mock_db):
        """TC-REP-001: GET /reports/settlement/2026-W12 → 200 with full report."""
        mock_db.asset_deduction_ledger.aggregate = MagicMock(return_value=_async_iter([]))
        mock_db.vendor_liabilities.aggregate = MagicMock(return_value=_async_iter([]))
        mock_db.rider_carry_forwards.aggregate = MagicMock(return_value=_async_iter([]))

        r = await client.get("/api/reports/settlement/2026-W12")
        assert r.status_code == 200
        body = r.json()
        assert body["weekCycle"] == "2026-W12"
        assert "riderBreakdown" in body
        assert "carryForwardSummary" in body

    async def test_rep_002_settlement_report_invalid_week(self, client, mock_db):
        """TC-REP-002: Invalid week string → 400."""
        r = await client.get("/api/reports/settlement/bad-week-format")
        assert r.status_code == 400

    async def test_rep_003_vendor_liability_report_with_data(self, client, mock_db):
        """TC-REP-003: GET /reports/vendor-liability → 200 with vendors and totals."""
        vendor_id_str = sid()
        mock_db.vendor_liabilities.aggregate = MagicMock(
            return_value=_async_iter([{
                "vendorId": vendor_id_str,
                "totalOutstanding": 5000.0,
                "totalRecovered": 2000.0,
                "netOutstanding": 3000.0,
                "pendingCount": 3,
                "partialCount": 1,
                "recoveredCount": 2,
            }])
        )

        r = await client.get("/api/reports/vendor-liability")
        assert r.status_code == 200
        body = r.json()
        assert "vendors" in body
        assert "totals" in body
        assert body["totals"]["totalOutstanding"] == 5000.0

    async def test_rep_004_vendor_liability_report_empty(self, client, mock_db):
        """TC-REP-004: No vendor liabilities → empty vendors list, zero totals."""
        mock_db.vendor_liabilities.aggregate = MagicMock(return_value=_async_iter([]))

        r = await client.get("/api/reports/vendor-liability")
        assert r.status_code == 200
        body = r.json()
        assert body["vendors"] == []
        assert body["totals"]["totalOutstanding"] == 0

    async def test_rep_005_audit_trail_with_logs(self, client, mock_db):
        """TC-REP-005: Audit trail for entity with logs → 200 with totalEntries."""
        entity_id = sid()
        log_doc = {
            "_id": oid(),
            "entityId": entity_id,
            "entityType": "issuance",
            "action": "issued",
            "performedBy": "LMD",
            "performedByUserId": None,
            "beforeValue": {},
            "afterValue": {},
            "reason": "Asset issued to rider",
            "timestamp": datetime.now(timezone.utc),
        }
        mock_db.asset_audit_logs.find = MagicMock(return_value=_cursor([log_doc]))

        r = await client.get(f"/api/reports/audit/{entity_id}")
        assert r.status_code == 200
        body = r.json()
        assert body["entityId"] == entity_id
        assert body["totalEntries"] == 1

    async def test_rep_006_audit_trail_no_logs(self, client, mock_db):
        """TC-REP-006: Entity with no audit logs → totalEntries=0, empty logs."""
        mock_db.asset_audit_logs.find = MagicMock(return_value=_cursor([]))

        r = await client.get(f"/api/reports/audit/{sid()}")
        assert r.status_code == 200
        body = r.json()
        assert body["totalEntries"] == 0
        assert body["logs"] == []


# ══════════════════════════════════════════════════════════════════════════════
# TC-VEN  Vendors
# ══════════════════════════════════════════════════════════════════════════════

class TestVendors:

    async def test_ven_001_get_liabilities(self, client, mock_db):
        """TC-VEN-001: GET vendor liabilities → 200 with list of liabilities."""
        vendor_id = oid()
        liability = {
            "_id": oid(), "vendorId": vendor_id, "issuanceId": oid(),
            "riderId": oid(), "outstandingAmount": 500.0,
            "amountRecovered": 0.0, "status": "pending",
            "passedAt": datetime.now(timezone.utc),
            "weekCycle": "2026-W12",
            "createdAt": datetime.now(timezone.utc),
        }
        mock_db.vendor_liabilities.find = MagicMock(return_value=_cursor([liability]))

        r = await client.get(f"/api/vendors/{vendor_id}/liabilities")
        assert r.status_code == 200
        assert len(r.json()) == 1

    async def test_ven_002_get_liabilities_invalid_id(self, client, mock_db):
        """TC-VEN-002: Invalid vendor_id → 400."""
        r = await client.get("/api/vendors/not-valid/liabilities")
        assert r.status_code == 400

    async def test_ven_003_get_liabilities_empty(self, client, mock_db):
        """TC-VEN-003: Vendor with no liabilities → empty list."""
        mock_db.vendor_liabilities.find = MagicMock(return_value=_cursor([]))

        r = await client.get(f"/api/vendors/{sid()}/liabilities")
        assert r.status_code == 200
        assert r.json() == []

    async def test_ven_004_settle_vendor_full(self, client, mock_db):
        """TC-VEN-004: Full settlement — amountPaid >= outstanding → amountUnused=0."""
        vendor_id = oid()
        liability = {
            "_id": oid(), "vendorId": vendor_id, "issuanceId": oid(),
            "riderId": oid(), "outstandingAmount": 500.0,
            "amountRecovered": 0.0, "status": "pending",
            "passedAt": datetime.now(timezone.utc),
            "weekCycle": "2026-W12",
            "createdAt": datetime.now(timezone.utc),
        }
        mock_db.vendor_liabilities.find = MagicMock(return_value=_cursor([liability]))

        r = await client.post(f"/api/vendors/{vendor_id}/settle", json={
            "amountPaid": 500.0,
            "note": "Full payment",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["amountApplied"] == 500.0
        assert body["amountUnused"] == 0.0
        assert body["liabilitiesSettled"][0]["newStatus"] == "recovered"

    async def test_ven_005_settle_vendor_partial(self, client, mock_db):
        """TC-VEN-005: Partial payment → liability marked partially_recovered."""
        vendor_id = oid()
        liability = {
            "_id": oid(), "vendorId": vendor_id, "issuanceId": oid(),
            "riderId": oid(), "outstandingAmount": 1000.0,
            "amountRecovered": 0.0, "status": "pending",
            "passedAt": datetime.now(timezone.utc),
            "weekCycle": "2026-W12",
            "createdAt": datetime.now(timezone.utc),
        }
        mock_db.vendor_liabilities.find = MagicMock(return_value=_cursor([liability]))

        r = await client.post(f"/api/vendors/{vendor_id}/settle", json={
            "amountPaid": 300.0,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["amountApplied"] == 300.0
        assert body["liabilitiesSettled"][0]["newStatus"] == "partially_recovered"

    async def test_ven_006_settle_vendor_invalid_id(self, client, mock_db):
        """TC-VEN-006: Invalid vendor_id → 400."""
        r = await client.post("/api/vendors/bad-id/settle", json={"amountPaid": 500.0})
        assert r.status_code == 400

    async def test_ven_007_settle_vendor_zero_amount(self, client, mock_db):
        """TC-VEN-007: amountPaid=0 (must be > 0) → 422."""
        r = await client.post(f"/api/vendors/{sid()}/settle", json={"amountPaid": 0.0})
        assert r.status_code == 422

    async def test_ven_008_settle_vendor_no_liabilities(self, client, mock_db):
        """TC-VEN-008: No pending liabilities → amountUnused equals amountPaid."""
        mock_db.vendor_liabilities.find = MagicMock(return_value=_cursor([]))

        r = await client.post(f"/api/vendors/{sid()}/settle", json={"amountPaid": 500.0})
        assert r.status_code == 200
        body = r.json()
        assert body["amountUnused"] == 500.0
        assert body["liabilitiesSettled"] == []

    async def test_ven_009_settle_vendor_negative_amount(self, client, mock_db):
        """TC-VEN-009: amountPaid < 0 → 422."""
        r = await client.post(f"/api/vendors/{sid()}/settle", json={"amountPaid": -100.0})
        assert r.status_code == 422


# ══════════════════════════════════════════════════════════════════════════════
# TC-ADM  Admin Override
# ══════════════════════════════════════════════════════════════════════════════

class TestAdminWaive:

    async def test_adm_001_waive_active_issuance(self, client, mock_db):
        """TC-ADM-001: Waive active issuance → 200 with previousOutstandingBalance."""
        iss = issuance_doc(status="active", outstanding=500.0)
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/waive/{iss['_id']}", json={
            "reason": "Rider terminated",
            "performed_by_user_id": "finance_01",
        })
        assert r.status_code == 200
        body = r.json()
        assert body["message"] == "Issuance waived successfully"
        assert body["previousOutstandingBalance"] == 500.0

    async def test_adm_002_waive_not_found(self, client, mock_db):
        """TC-ADM-002: Issuance not found → 404."""
        mock_db.asset_issuances.find_one = AsyncMock(return_value=None)

        r = await client.post(f"/api/admin/waive/{sid()}", json={"reason": "test"})
        assert r.status_code == 404

    async def test_adm_003_waive_already_written_off(self, client, mock_db):
        """TC-ADM-003: Already written_off issuance → 400."""
        iss = issuance_doc(status="written_off")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/waive/{iss['_id']}", json={"reason": "dup"})
        assert r.status_code == 400

    async def test_adm_004_waive_already_completed(self, client, mock_db):
        """TC-ADM-004: Already completed issuance → 400."""
        iss = issuance_doc(status="completed")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/waive/{iss['_id']}", json={"reason": "done"})
        assert r.status_code == 400

    async def test_adm_005_waive_invalid_issuance_id(self, client, mock_db):
        """TC-ADM-005: Invalid issuance_id → 400."""
        r = await client.post("/api/admin/waive/bad-id", json={"reason": "test"})
        assert r.status_code == 400

    async def test_adm_006_waive_empty_reason(self, client, mock_db):
        """TC-ADM-006: Empty reason (min_length=1) → 422."""
        r = await client.post(f"/api/admin/waive/{sid()}", json={"reason": ""})
        assert r.status_code == 422

    async def test_adm_007_waive_replaced_issuance(self, client, mock_db):
        """TC-ADM-007: Waive a 'replaced' status issuance → 200 (replaced is not in blocklist)."""
        iss = issuance_doc(status="replaced", outstanding=200.0)
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/waive/{iss['_id']}", json={"reason": "mid-cycle replacement"})
        assert r.status_code == 200


class TestAdminDeductionAdjust:

    async def test_adm_008_adjust_deduction_valid(self, client, mock_db):
        """TC-ADM-008: Adjust ledger deduction amount → 200 with updated ledger."""
        iss_id = oid()
        iss = issuance_doc(issuance_id=iss_id, outstanding=1000.0)
        l_id = oid()
        l = ledger_doc(ledger_id=l_id, issuance_id=iss_id)
        updated = serialized_ledger_doc(ledger_id=l_id, issuance_id=iss_id)
        mock_db.asset_deduction_ledger.find_one = AsyncMock(side_effect=[l, updated])
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.patch(f"/api/admin/deduction/{l_id}", json={
            "amountDeducted": 200.0,
            "reason": "Correction",
        })
        assert r.status_code == 200

    async def test_adm_009_adjust_deduction_not_found(self, client, mock_db):
        """TC-ADM-009: Ledger not found → 404."""
        mock_db.asset_deduction_ledger.find_one = AsyncMock(return_value=None)

        r = await client.patch(f"/api/admin/deduction/{sid()}", json={
            "amountDeducted": 100.0, "reason": "test",
        })
        assert r.status_code == 404

    async def test_adm_010_adjust_deduction_invalid_id(self, client, mock_db):
        """TC-ADM-010: Invalid ledger_id → 400."""
        r = await client.patch("/api/admin/deduction/bad-id", json={
            "amountDeducted": 100.0, "reason": "test",
        })
        assert r.status_code == 400

    async def test_adm_011_adjust_deduction_negative_amount(self, client, mock_db):
        """TC-ADM-011: amountDeducted < 0 (ge=0 constraint) → 422."""
        r = await client.patch(f"/api/admin/deduction/{sid()}", json={
            "amountDeducted": -50.0, "reason": "test",
        })
        assert r.status_code == 422

    async def test_adm_012_adjust_deduction_zero_amount(self, client, mock_db):
        """TC-ADM-012: amountDeducted=0 (fully skipped) → 200."""
        iss_id = oid()
        iss = issuance_doc(issuance_id=iss_id, outstanding=1000.0)
        l_id = oid()
        l = ledger_doc(ledger_id=l_id, issuance_id=iss_id)
        updated = serialized_ledger_doc(ledger_id=l_id, issuance_id=iss_id)
        mock_db.asset_deduction_ledger.find_one = AsyncMock(side_effect=[l, updated])
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.patch(f"/api/admin/deduction/{l_id}", json={
            "amountDeducted": 0.0,
            "reason": "Skipped this cycle",
        })
        assert r.status_code == 200


class TestAdminFlagAbuse:

    async def test_adm_013_flag_abuse_vendor_issuance(self, client, mock_db):
        """TC-ADM-013: Flag VENDOR issuance as SELF absorption → 200."""
        iss = issuance_doc(absorption="VENDOR", status="active")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/flag-abuse/{iss['_id']}", json={
            "reason": "Rider caused damage",
        })
        assert r.status_code == 200
        body = r.json()
        assert "SELF" in body["message"]
        assert body["previousAbsorptionType"] == "VENDOR"

    async def test_adm_014_flag_abuse_already_self(self, client, mock_db):
        """TC-ADM-014: Issuance already SELF absorption → 400."""
        iss = issuance_doc(absorption="SELF", status="active")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/flag-abuse/{iss['_id']}", json={
            "reason": "Already self"
        })
        assert r.status_code == 400

    async def test_adm_015_flag_abuse_completed_status(self, client, mock_db):
        """TC-ADM-015: Completed issuance cannot be flagged → 400."""
        iss = issuance_doc(absorption="VENDOR", status="completed")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/flag-abuse/{iss['_id']}", json={
            "reason": "Too late"
        })
        assert r.status_code == 400

    async def test_adm_016_flag_abuse_not_found(self, client, mock_db):
        """TC-ADM-016: Issuance not found → 404."""
        mock_db.asset_issuances.find_one = AsyncMock(return_value=None)

        r = await client.post(f"/api/admin/flag-abuse/{sid()}", json={"reason": "test"})
        assert r.status_code == 404

    async def test_adm_017_flag_abuse_invalid_id(self, client, mock_db):
        """TC-ADM-017: Invalid issuance_id → 400."""
        r = await client.post("/api/admin/flag-abuse/not-an-id", json={"reason": "x"})
        assert r.status_code == 400

    async def test_adm_018_flag_abuse_vendor_passed_reactivates(self, client, mock_db):
        """TC-ADM-018: vendor_passed issuance gets reactivated on flag-abuse → 200."""
        iss = issuance_doc(absorption="VENDOR", status="vendor_passed")
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post(f"/api/admin/flag-abuse/{iss['_id']}", json={
            "reason": "Rider damage confirmed"
        })
        assert r.status_code == 200


class TestAdminYearEndWriteOff:

    async def test_adm_019_year_end_dry_run(self, client, mock_db):
        """TC-ADM-019: Dry run → returns issuancesToWriteOff without committing."""
        iss = issuance_doc(absorption="SELF", status="active")
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.orders.count_documents = AsyncMock(return_value=0)

        r = await client.post("/api/admin/year-end-writeoff", json={
            "financialYearEnd": "2025-03-31T00:00:00Z",
            "dry_run": True,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["dryRun"] is True
        assert len(body["issuancesToWriteOff"]) == 1
        mock_db.asset_issuances.update_one.assert_not_called()

    async def test_adm_020_year_end_actual_writeoff(self, client, mock_db):
        """TC-ADM-020: Actual write-off commits to DB → writtenOff list populated."""
        iss = issuance_doc(absorption="SELF", status="active")
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.orders.count_documents = AsyncMock(return_value=0)
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss)

        r = await client.post("/api/admin/year-end-writeoff", json={
            "financialYearEnd": "2025-03-31T00:00:00Z",
            "dry_run": False,
        })
        assert r.status_code == 200
        body = r.json()
        assert len(body["writtenOff"]) == 1
        mock_db.asset_issuances.update_one.assert_called()

    async def test_adm_021_year_end_active_rider_excluded(self, client, mock_db):
        """TC-ADM-021: Rider still active (has orders) → excluded from write-off."""
        iss = issuance_doc(absorption="SELF", status="active")
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.orders.count_documents = AsyncMock(return_value=5)

        r = await client.post("/api/admin/year-end-writeoff", json={
            "financialYearEnd": "2025-03-31T00:00:00Z",
            "dry_run": False,
        })
        assert r.status_code == 200
        assert len(r.json()["writtenOff"]) == 0

    async def test_adm_022_year_end_no_qualifying_issuances(self, client, mock_db):
        """TC-ADM-022: No SELF issuances found → count=0."""
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([]))

        r = await client.post("/api/admin/year-end-writeoff", json={
            "financialYearEnd": "2025-03-31T00:00:00Z",
            "dry_run": True,
        })
        assert r.status_code == 200
        assert r.json()["count"] == 0

    async def test_adm_023_year_end_multiple_riders(self, client, mock_db):
        """TC-ADM-023: Multiple SELF issuances, one inactive rider, one active → only inactive written off."""
        iss1 = issuance_doc(absorption="SELF", status="active")
        iss2 = issuance_doc(absorption="SELF", status="active")
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss1, iss2]))
        mock_db.orders.count_documents = AsyncMock(side_effect=[0, 5])
        mock_db.asset_issuances.find_one = AsyncMock(return_value=iss1)

        r = await client.post("/api/admin/year-end-writeoff", json={
            "financialYearEnd": "2025-03-31T00:00:00Z",
            "dry_run": True,
        })
        assert r.status_code == 200
        assert r.json()["count"] == 1


# ══════════════════════════════════════════════════════════════════════════════
# TC-RIDER  Rider View
# ══════════════════════════════════════════════════════════════════════════════

class TestRiderView:

    async def test_rider_001_get_assets_with_issuances(self, client, mock_db):
        """TC-RIDER-001: Rider with one active issuance → correct balance."""
        rider_id = oid()
        a = asset_doc()
        iss = issuance_doc(rider_id=rider_id, asset_id=a["_id"], outstanding=750.0, deduction=250.0)
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.get(f"/api/rider/{rider_id}/assets")
        assert r.status_code == 200
        body = r.json()
        assert body["riderId"] == str(rider_id)
        assert len(body["assets"]) == 1
        assert body["totalOutstanding"] == 750.0
        assert body["assets"][0]["deductionThisCycle"] == 250.0

    async def test_rider_002_invalid_rider_id(self, client, mock_db):
        """TC-RIDER-002: Invalid rider_id → 400."""
        r = await client.get("/api/rider/not-a-valid-id/assets")
        assert r.status_code == 400

    async def test_rider_003_no_issuances(self, client, mock_db):
        """TC-RIDER-003: Rider with no issuances → empty assets, totalOutstanding=0."""
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([]))

        r = await client.get(f"/api/rider/{sid()}/assets")
        assert r.status_code == 200
        body = r.json()
        assert body["assets"] == []
        assert body["totalOutstanding"] == 0.0

    async def test_rider_004_multiple_issuances_total_summed(self, client, mock_db):
        """TC-RIDER-004: Multiple issuances → totalOutstanding correctly summed."""
        rider_id = oid()
        a1 = asset_doc(asset_id=oid())
        a2 = asset_doc(asset_id=oid())
        iss1 = issuance_doc(rider_id=rider_id, asset_id=a1["_id"], outstanding=750.0)
        iss2 = issuance_doc(rider_id=rider_id, asset_id=a2["_id"], outstanding=250.0)
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss1, iss2]))
        mock_db.asset_master.find_one = AsyncMock(side_effect=[a1, a2])

        r = await client.get(f"/api/rider/{rider_id}/assets")
        assert r.status_code == 200
        body = r.json()
        assert len(body["assets"]) == 2
        assert body["totalOutstanding"] == 1000.0

    async def test_rider_005_unknown_asset_fallback_name(self, client, mock_db):
        """TC-RIDER-005: Issuance references missing asset → name='Unknown Asset'."""
        rider_id = oid()
        iss = issuance_doc(rider_id=rider_id, outstanding=500.0)
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.asset_master.find_one = AsyncMock(return_value=None)

        r = await client.get(f"/api/rider/{rider_id}/assets")
        assert r.status_code == 200
        assert r.json()["assets"][0]["name"] == "Unknown Asset"

    async def test_rider_006_cycles_remaining_calculation(self, client, mock_db):
        """TC-RIDER-006: cyclesRemaining = totalCycles - cyclesCompleted."""
        rider_id = oid()
        a = asset_doc()
        iss = issuance_doc(
            rider_id=rider_id, asset_id=a["_id"],
            cycles=4, completed=2, outstanding=500.0
        )
        mock_db.asset_issuances.find = MagicMock(return_value=_cursor([iss]))
        mock_db.asset_master.find_one = AsyncMock(return_value=a)

        r = await client.get(f"/api/rider/{rider_id}/assets")
        assert r.status_code == 200
        asset_entry = r.json()["assets"][0]
        assert asset_entry["cyclesRemaining"] == 2
        assert asset_entry["cyclesCompleted"] == 2
