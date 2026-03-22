# Fairdeal Rider Asset Management & Settlement System

A production-grade FastAPI service for managing asset issuance to delivery riders,
weekly deduction settlements, vendor liability tracking, and full audit logging.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Tech Stack](#2-tech-stack)
3. [Project Structure](#3-project-structure)
4. [Setup Instructions](#4-setup-instructions)
5. [Running the Server](#5-running-the-server)
6. [API Documentation](#6-api-documentation)
7. [Settlement Engine Explanation](#7-settlement-engine-explanation)
8. [Cron Job Setup](#8-cron-job-setup)
9. [Running Tests](#9-running-tests)

---

## 1. Project Overview

This service handles the full lifecycle of rider assets at Fairdeal.market:

- **Asset Master**: Create and manage inventory of issuable assets (delivery bags, jackets, etc.)
- **Issuances**: Issue assets to riders with FLAT or EMI deduction plans (up to 4 cycles)
- **Weekly Settlement**: Automatically deduct asset costs from rider payouts each week using a strict priority system
- **Vendor Liabilities**: Track and settle outstanding amounts owed to vendors when riders go inactive
- **Admin Overrides**: Finance and LMD teams can waive balances, adjust deductions, flag abuse, and perform year-end write-offs
- **Audit Logging**: Every action is recorded with before/after values and actor identity

### Key Business Rules

| Rule | Detail |
|------|--------|
| EMI max cycles | Hard cap of 4 |
| Deduction per cycle | `floor(costPrice / totalCycles)` |
| Payout floor | Never goes negative (asserted in code) |
| Inactive + VENDOR asset | Outstanding passed to `vendor_liabilities` immediately |
| Inactive + SELF asset | Balance kept as carry-forward, recovered when rider returns |
| Year-end write-off | SELF defaults written off if rider never returns |

---

## 2. Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python 3.11+ + FastAPI |
| ASGI Server | Uvicorn |
| Database | MongoDB Atlas (async via Motor) |
| Scheduler | APScheduler (AsyncIOScheduler) |
| CSV Parsing | Pandas |
| Validation | Pydantic v2 |
| Testing | pytest + pytest-asyncio |

---

## 3. Project Structure

```
rider-asset-management/
├── main.py                      # FastAPI app, lifespan, router registration
├── config.py                    # DB connection, index creation
├── requirements.txt
├── README.md
├── models/
│   ├── asset_master.py          # AssetMaster, AssetMasterCreate, AssetMasterUpdate
│   ├── issuance.py              # AssetIssuance, AssetIssuanceCreate, BulkIssuanceRow
│   ├── deduction_ledger.py      # AssetDeductionLedger
│   ├── vendor_liability.py      # VendorLiability, VendorSettleRequest
│   └── audit_log.py             # AssetAuditLog
├── routes/
│   ├── assets.py                # CRUD for asset_master
│   ├── issuances.py             # Issue, bulk upload, replacement
│   ├── settlement.py            # Run weekly settlement, status query
│   ├── vendors.py               # Vendor liabilities & settlement
│   ├── admin.py                 # Waive, adjust, flag abuse, year-end write-off
│   ├── rider.py                 # Rider asset view
│   └── reports.py               # Settlement report, vendor summary, audit trail
├── services/
│   ├── settlement_engine.py     # Core weekly settlement orchestrator
│   ├── deduction_calculator.py  # Pure calculation logic (no DB)
│   ├── vendor_service.py        # Vendor liability queries & settlement
│   └── audit_service.py         # Audit log writes & reads
├── cron/
│   └── weekly_settlement.py     # APScheduler job (Saturday 23:00 UTC)
└── tests/
    ├── test_settlement_engine.py
    └── test_issuance_validation.py
```

---

## 4. Setup Instructions

### Prerequisites

- Python 3.11 or higher
- A MongoDB Atlas cluster (or local MongoDB instance)
- `pip` package manager

### Step 1: Clone / navigate to the project

```bash
cd rider-asset-management
```

### Step 2: Create a virtual environment

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate
```

### Step 3: Install dependencies

```bash
pip install -r requirements.txt
```

### Step 4: Configure environment variables

Create a `.env` file in the project root:

```env
MONGODB_URL=mongodb+srv://<username>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
```

For local MongoDB:

```env
MONGODB_URL=mongodb://localhost:27017
```

The service connects to database **`db`** automatically.

### Step 5: Verify existing collections

The service uses the following pre-existing collections in your `db` database:
- `riders` — rider profiles
- `orders` — delivery orders (used for activity checks and earnings)
- `orderhistories` — order history

New collections are created automatically on first use (all prefixed with `asset`):
- `asset_master`
- `asset_issuances`
- `asset_deduction_ledger`
- `vendor_liabilities`
- `asset_audit_logs`
- `rider_carry_forwards`

---

## 5. Running the Server

### Development (with auto-reload)

```bash
python main.py
```

Or directly with uvicorn:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### Production

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

The API will be available at:
- **API Base**: `http://localhost:8000`
- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`
- **Health Check**: `http://localhost:8000/health`

---

## 6. API Documentation

### Asset Master APIs

#### Create Asset
```bash
curl -X POST http://localhost:8000/api/assets \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Delivery Bag",
    "costPrice": 1000,
    "absorptionType": "VENDOR",
    "vendorId": "64a1b2c3d4e5f6a7b8c9d0e1",
    "stock": 50
  }'
```

#### List All Assets
```bash
curl http://localhost:8000/api/assets
# Filter by status:
curl "http://localhost:8000/api/assets?status=active"
```

#### Update Asset
```bash
curl -X PATCH http://localhost:8000/api/assets/64a1b2c3d4e5f6a7b8c9d0e1 \
  -H "Content-Type: application/json" \
  -d '{"stock": 30, "costPrice": 950}'
```

#### Soft Delete Asset
```bash
curl -X DELETE http://localhost:8000/api/assets/64a1b2c3d4e5f6a7b8c9d0e1
```

---

### Issuance APIs

#### Issue Asset to Rider
```bash
curl -X POST http://localhost:8000/api/issuances \
  -H "Content-Type: application/json" \
  -d '{
    "riderId": "64a1b2c3d4e5f6a7b8c9d0e2",
    "assetId": "64a1b2c3d4e5f6a7b8c9d0e1",
    "reconciliationType": "EMI",
    "totalCycles": 4
  }'
```

Force duplicate issuance:
```bash
curl -X POST "http://localhost:8000/api/issuances?confirm=true" \
  -H "Content-Type: application/json" \
  -d '{
    "riderId": "64a1b2c3d4e5f6a7b8c9d0e2",
    "assetId": "64a1b2c3d4e5f6a7b8c9d0e1",
    "reconciliationType": "EMI",
    "totalCycles": 4
  }'
```

#### Get Rider's Issuances
```bash
curl http://localhost:8000/api/issuances/64a1b2c3d4e5f6a7b8c9d0e2
# Filter by status:
curl "http://localhost:8000/api/issuances/64a1b2c3d4e5f6a7b8c9d0e2?status=active"
```

#### Bulk Upload via CSV
```bash
curl -X POST http://localhost:8000/api/issuances/bulk \
  -F "file=@issuances.csv"
```

CSV format:
```csv
riderId,assetId,reconciliationType,totalCycles
64a1b2c3d4e5f6a7b8c9d0e2,64a1b2c3d4e5f6a7b8c9d0e1,EMI,4
64a1b2c3d4e5f6a7b8c9d0f3,64a1b2c3d4e5f6a7b8c9d0e1,FLAT,1
```

#### Replace an Issuance (LMD)
```bash
# Waive remaining balance
curl -X POST "http://localhost:8000/api/issuances/64a1b2c3d4e5f6a7b8c9d0e3/replace?mid_balance_action=waive_remaining"

# Pass remaining balance to vendor
curl -X POST "http://localhost:8000/api/issuances/64a1b2c3d4e5f6a7b8c9d0e3/replace?mid_balance_action=pass_to_vendor"
```

---

### Settlement APIs

#### Run Weekly Settlement
```bash
curl -X POST http://localhost:8000/api/settlement/run-weekly \
  -H "Content-Type: application/json" \
  -d '{
    "weekStart": "2026-03-16T00:00:00Z",
    "weekEnd": "2026-03-22T23:59:59Z",
    "riderEarnings": {
      "64a1b2c3d4e5f6a7b8c9d0e2": 1500.0,
      "64a1b2c3d4e5f6a7b8c9d0f3": 800.0,
      "64a1b2c3d4e5f6a7b8c9d0f4": 0.0
    }
  }'
```

#### Get Settlement Status
```bash
curl http://localhost:8000/api/settlement/status/2026-W12
```

---

### Vendor Liability APIs

#### Get Vendor Liabilities
```bash
curl http://localhost:8000/api/vendors/64a1b2c3d4e5f6a7b8c9d0e1/liabilities
```

#### Record Vendor Payout
```bash
curl -X POST http://localhost:8000/api/vendors/64a1b2c3d4e5f6a7b8c9d0e1/settle \
  -H "Content-Type: application/json" \
  -d '{
    "amountPaid": 5000.0,
    "note": "Monthly vendor reconciliation"
  }'
```

---

### Admin Override APIs

#### Waive Issuance Balance (Finance)
```bash
curl -X POST http://localhost:8000/api/admin/waive/64a1b2c3d4e5f6a7b8c9d0e3 \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Rider left company, balance written off per policy",
    "performed_by_user_id": "64a1b2c3d4e5f6a7b8c9d0aa"
  }'
```

#### Manual Deduction Adjustment (Finance)
```bash
curl -X PATCH http://localhost:8000/api/admin/deduction/64a1b2c3d4e5f6a7b8c9d0e4 \
  -H "Content-Type: application/json" \
  -d '{
    "amountDeducted": 200.0,
    "reason": "Correcting over-deduction from last week",
    "performed_by_user_id": "64a1b2c3d4e5f6a7b8c9d0aa"
  }'
```

#### Flag Abuse — Force SELF Absorption (LMD)
```bash
curl -X POST http://localhost:8000/api/admin/flag-abuse/64a1b2c3d4e5f6a7b8c9d0e3 \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Rider damaged asset intentionally",
    "performed_by_user_id": "64a1b2c3d4e5f6a7b8c9d0bb"
  }'
```

#### Year-End Write-Off (Finance)
```bash
# Dry run first to see what will be written off
curl -X POST http://localhost:8000/api/admin/year-end-writeoff \
  -H "Content-Type: application/json" \
  -d '{
    "financialYearEnd": "2026-03-31T00:00:00Z",
    "dry_run": true
  }'

# Execute the write-off
curl -X POST http://localhost:8000/api/admin/year-end-writeoff \
  -H "Content-Type: application/json" \
  -d '{
    "financialYearEnd": "2026-03-31T00:00:00Z",
    "dry_run": false,
    "performed_by_user_id": "64a1b2c3d4e5f6a7b8c9d0aa"
  }'
```

---

### Rider View API

#### Get Rider's Assets and Outstanding Balance
```bash
curl http://localhost:8000/api/rider/64a1b2c3d4e5f6a7b8c9d0e2/assets
```

Response:
```json
{
  "riderId": "64a1b2c3d4e5f6a7b8c9d0e2",
  "assets": [
    {
      "issuanceId": "64a1b2c3d4e5f6a7b8c9d0e3",
      "assetId": "64a1b2c3d4e5f6a7b8c9d0e1",
      "name": "Delivery Bag",
      "outstandingBalance": 500.0,
      "deductionThisCycle": 250.0,
      "cyclesRemaining": 2,
      "totalCycles": 4,
      "cyclesCompleted": 2,
      "absorptionType": "VENDOR",
      "status": "active",
      "issuedAt": "2026-01-15T10:30:00+00:00"
    }
  ],
  "totalOutstanding": 500.0
}
```

---

### Report APIs

#### Full Weekly Settlement Report
```bash
curl http://localhost:8000/api/reports/settlement/2026-W12
```

#### All Vendor Liabilities Summary
```bash
curl http://localhost:8000/api/reports/vendor-liability
```

#### Audit Trail for Any Entity
```bash
curl http://localhost:8000/api/reports/audit/64a1b2c3d4e5f6a7b8c9d0e3
```

---

## 7. Settlement Engine Explanation

The settlement engine (`services/settlement_engine.py`) is the core of this system.

### Weekly Cycle

Every Saturday at 23:00 UTC (or on-demand via API), the engine processes all riders
with active asset issuances.

### Processing Flow

```
For each rider:
  1. Check if rider is ACTIVE this week
     → Query orders collection for orders with rider = riderId in [weekStart, weekEnd]
     → If count > 0: rider is ACTIVE
     → If count == 0: rider is INACTIVE

  2. If INACTIVE:
     → VENDOR issuances: create vendor_liabilities record, mark issuance as vendor_passed
     → SELF issuances: create/update skipped ledger entry + carry_forward record

  3. If ACTIVE:
     a. Collect P0 tasks: pending carry-forwards (SELF only), sorted by weekCycle ASC
     b. Collect P1a tasks: active SELF issuances, sorted by issuedAt ASC (FIFO)
     c. Collect P1b tasks: active VENDOR issuances, sorted by issuedAt ASC (FIFO)
     d. Merge into ordered list: [P0...] + [P1a...] + [P1b...]

  4. Apply deductions in order against available earnings:
     - If earnings >= amount_due → fully deduct, payout reduces
     - If 0 < earnings < amount_due → deduct what's available (partial), carry forward rest
     - If earnings == 0 → skip, carry forward full amount_due

  5. For each deduction result:
     - Insert record into asset_deduction_ledger
     - Update issuance (totalRecovered, outstandingBalance, cyclesCompleted, status)
     - Create/update rider_carry_forwards if shortfall > 0
     - Log to asset_audit_logs

  6. assert final_payout >= 0 (hard guarantee, never goes negative)
```

### Priority Order

| Priority | Description |
|----------|-------------|
| **P0** | Negative SELF carry-forward (returning rider recovery) — oldest weekCycle first |
| **P1a** | SELF absorption issuances — oldest issuedAt first (FIFO) |
| **P1b** | VENDOR absorption issuances — oldest issuedAt first (FIFO) |

### Deduction Calculation

```
deductionPerCycle = floor(costPrice / totalCycles)

Example:
  costPrice = 1000, totalCycles = 4
  deductionPerCycle = floor(1000 / 4) = 250
```

Note: Using `floor()` means the total recovered may be up to `totalCycles - 1` less than
`costPrice` due to rounding. This is by design.

### Carry-Forward Logic

When a rider cannot fully pay a deduction cycle:
- The shortfall is stored in `rider_carry_forwards` with `status = pending`
- Next week, when the rider earns enough, the carry-forward is recovered at **P0 priority**
- The payout floor is always 0 — riders never owe money back

---

## 8. Cron Job Setup

The weekly settlement runs automatically via APScheduler.

### Schedule

- **Trigger**: Every **Saturday at 23:00 UTC**
- **Misfire grace**: 1 hour (if server was down at trigger time, it runs within 1 hour of coming back up)

### How it works

The cron job in `cron/weekly_settlement.py`:
1. Calculates the current ISO week boundaries (Monday–Sunday)
2. Queries `orders` to compute per-rider earnings for that week
3. Calls `SettlementEngine.run_weekly_settlement()`
4. Logs the result (success counts, errors)

### APScheduler is started automatically

The scheduler is registered in `main.py`'s lifespan context:

```python
setup_cron_jobs(scheduler)
scheduler.start()
```

No additional setup is required — it starts with the server.

### Manual trigger

To run settlement on-demand (e.g. for testing or catch-up):

```bash
curl -X POST http://localhost:8000/api/settlement/run-weekly \
  -H "Content-Type: application/json" \
  -d '{
    "weekStart": "2026-03-16T00:00:00Z",
    "weekEnd": "2026-03-22T23:59:59Z",
    "riderEarnings": {}
  }'
```

Passing `riderEarnings: {}` causes the engine to treat all riders as earning 0
(inactive or zero-earnings logic applies).

---

## 9. Running Tests

```bash
# Install test dependencies (already in requirements.txt)
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_settlement_engine.py -v
pytest tests/test_issuance_validation.py -v

# Run with coverage
pip install pytest-cov
pytest tests/ -v --cov=. --cov-report=term-missing
```

### Test Coverage Summary

| Test | Description |
|------|-------------|
| `test_exact_earnings_full_deduction` | Rider earns exactly the deduction amount |
| `test_partial_earnings_carry_forward` | Rider earns less than deduction (partial) |
| `test_active_rider_zero_earnings_no_deduction` | Active rider earns 0 |
| `test_inactive_rider_vendor_asset_passed_to_vendor` | Inactive + VENDOR → vendor_liabilities |
| `test_inactive_rider_self_asset_carry_forward` | Inactive + SELF → carry forward |
| `test_returning_rider_carry_forward_p0_priority` | P0 carry-forward recovery |
| `test_vendor_partial_settlement` | Vendor pays less than liability |
| `TestDeductionCalculator.*` | Pure calculation unit tests |
| `test_issuance_rejected_when_stock_zero` | Hard block: stock = 0 |
| `test_issuance_model_rejects_cycles_over_4` | Hard block: cycles > 4 |
| `test_duplicate_issuance_returns_warning_without_confirm` | Duplicate warning |
| `test_duplicate_issuance_proceeds_with_confirm` | Duplicate with confirm=true |
| `test_issuance_rejected_when_asset_inactive` | Inactive asset block |
| `test_issuance_rejected_when_asset_not_found` | 404 asset not found |
| `test_flat_reconciliation_requires_single_cycle` | FLAT type validation |
| `TestBulkIssuanceRow.*` | Bulk upload row validation |

---

## Environment Variables Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `MONGODB_URL` | `mongodb://localhost:27017` | MongoDB connection string |

---

## MongoDB Collections Reference

| Collection | Purpose |
|-----------|---------|
| `asset_master` | Asset catalogue with pricing and stock |
| `asset_issuances` | Per-rider asset issuance records |
| `asset_deduction_ledger` | Weekly deduction entries per issuance |
| `vendor_liabilities` | Amounts owed to vendors for inactive riders |
| `asset_audit_logs` | Immutable audit trail for all actions |
| `rider_carry_forwards` | Shortfall carry-forwards pending recovery |
| `riders` | (existing) Rider profiles |
| `orders` | (existing) Delivery orders |
| `orderhistories` | (existing) Order history |
