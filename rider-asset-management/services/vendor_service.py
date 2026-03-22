from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from services.audit_service import AuditService


class VendorService:
    """Handles vendor liability queries and settlement recording."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db
        self.audit = AuditService(db)

    async def get_liabilities(self, vendor_id_str: str) -> List[Dict[str, Any]]:
        """Return all liability records for the given vendor."""
        vendor_oid = ObjectId(vendor_id_str)
        liabilities: List[Dict[str, Any]] = []
        cursor = self.db.vendor_liabilities.find({"vendorId": vendor_oid}).sort(
            "createdAt", -1
        )
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["vendorId"] = str(doc["vendorId"])
            doc["issuanceId"] = str(doc["issuanceId"])
            doc["riderId"] = str(doc["riderId"])
            liabilities.append(doc)
        return liabilities

    async def settle_vendor(
        self,
        vendor_id_str: str,
        amount_paid: float,
        note: str | None = None,
        performed_by_user_id: str | None = None,
    ) -> Dict[str, Any]:
        """
        Record a vendor payout against outstanding liabilities.

        Applies the payment against the oldest pending/partially_recovered
        liabilities in FIFO order.

        Returns a summary dict.
        """
        vendor_oid = ObjectId(vendor_id_str)
        now = datetime.now(timezone.utc)

        # Fetch outstanding liabilities oldest-first
        cursor = self.db.vendor_liabilities.find(
            {"vendorId": vendor_oid, "status": {"$in": ["pending", "partially_recovered"]}}
        ).sort("createdAt", 1)

        liabilities: List[Dict[str, Any]] = []
        async for doc in cursor:
            liabilities.append(doc)

        remaining_payment = amount_paid
        applied: List[Dict[str, Any]] = []

        for liability in liabilities:
            if remaining_payment <= 0:
                break

            outstanding = liability["outstandingAmount"] - liability.get("amountRecovered", 0.0)
            if outstanding <= 0:
                continue

            before = dict(liability)

            if remaining_payment >= outstanding:
                # Fully settle this liability
                payment_applied = outstanding
                new_recovered = liability.get("amountRecovered", 0.0) + payment_applied
                new_status = "recovered"
                new_outstanding = 0.0
            else:
                payment_applied = remaining_payment
                new_recovered = liability.get("amountRecovered", 0.0) + payment_applied
                new_status = "partially_recovered"
                new_outstanding = liability["outstandingAmount"] - new_recovered

            remaining_payment -= payment_applied

            await self.db.vendor_liabilities.update_one(
                {"_id": liability["_id"]},
                {
                    "$set": {
                        "amountRecovered": new_recovered,
                        "status": new_status,
                        "updatedAt": now,
                    }
                },
            )

            after = {
                **before,
                "amountRecovered": new_recovered,
                "status": new_status,
            }

            await self.audit.log(
                entity_type="vendor_liability",
                entity_id=str(liability["_id"]),
                action="deducted",
                performed_by="Finance",
                performed_by_user_id=performed_by_user_id,
                before_value={
                    k: str(v) if isinstance(v, ObjectId) else v
                    for k, v in before.items()
                },
                after_value={
                    k: str(v) if isinstance(v, ObjectId) else v
                    for k, v in after.items()
                },
                reason=note or f"Vendor settlement payment of {amount_paid}",
            )

            applied.append(
                {
                    "liabilityId": str(liability["_id"]),
                    "amountApplied": payment_applied,
                    "newStatus": new_status,
                }
            )

        return {
            "vendorId": vendor_id_str,
            "amountPaid": amount_paid,
            "amountApplied": amount_paid - remaining_payment,
            "amountUnused": remaining_payment,
            "liabilitiesSettled": applied,
        }

    async def get_vendor_liability_summary(self) -> List[Dict[str, Any]]:
        """Return aggregated liability summary across all vendors."""
        pipeline = [
            {
                "$group": {
                    "_id": "$vendorId",
                    "totalOutstanding": {"$sum": "$outstandingAmount"},
                    "totalRecovered": {"$sum": "$amountRecovered"},
                    "pendingCount": {
                        "$sum": {"$cond": [{"$eq": ["$status", "pending"]}, 1, 0]}
                    },
                    "partialCount": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "partially_recovered"]},
                                1,
                                0,
                            ]
                        }
                    },
                    "recoveredCount": {
                        "$sum": {"$cond": [{"$eq": ["$status", "recovered"]}, 1, 0]}
                    },
                }
            },
            {
                "$project": {
                    "vendorId": {"$toString": "$_id"},
                    "totalOutstanding": 1,
                    "totalRecovered": 1,
                    "netOutstanding": {
                        "$subtract": ["$totalOutstanding", "$totalRecovered"]
                    },
                    "pendingCount": 1,
                    "partialCount": 1,
                    "recoveredCount": 1,
                }
            },
        ]
        result = []
        async for doc in self.db.vendor_liabilities.aggregate(pipeline):
            doc.pop("_id", None)
            result.append(doc)
        return result
