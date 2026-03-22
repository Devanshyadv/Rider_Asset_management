import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
DATABASE_NAME = "db"

client: AsyncIOMotorClient = None


async def connect_db():
    global client
    client = AsyncIOMotorClient(MONGODB_URL)
    db = client[DATABASE_NAME]
    await create_indexes(db)
    return db


async def get_db():
    db = client[DATABASE_NAME]
    return db


async def close_db():
    global client
    if client:
        client.close()


async def create_indexes(db):
    # asset_master
    await db.asset_master.create_index("status")
    await db.asset_master.create_index("vendorId")

    # asset_issuances
    await db.asset_issuances.create_index("riderId")
    await db.asset_issuances.create_index("assetId")
    await db.asset_issuances.create_index("status")
    await db.asset_issuances.create_index([("riderId", 1), ("assetId", 1)])

    # asset_deduction_ledger
    await db.asset_deduction_ledger.create_index("riderId")
    await db.asset_deduction_ledger.create_index("issuanceId")
    await db.asset_deduction_ledger.create_index("weekCycle")
    await db.asset_deduction_ledger.create_index("status")

    # vendor_liabilities
    await db.vendor_liabilities.create_index("vendorId")
    await db.vendor_liabilities.create_index("issuanceId")
    await db.vendor_liabilities.create_index("status")
    await db.vendor_liabilities.create_index("weekCycle")

    # asset_audit_logs
    await db.asset_audit_logs.create_index("entityId")
    await db.asset_audit_logs.create_index("entityType")
    await db.asset_audit_logs.create_index("timestamp")

    # rider_carry_forwards
    await db.rider_carry_forwards.create_index("riderId")
    await db.rider_carry_forwards.create_index("issuanceId")
    await db.rider_carry_forwards.create_index("status")
    await db.rider_carry_forwards.create_index("weekCycle")
