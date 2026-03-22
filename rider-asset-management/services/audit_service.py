from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase


class AuditService:
    """Responsible for writing every action to asset_audit_logs."""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    async def log(
        self,
        entity_type: str,
        entity_id: str,
        action: str,
        performed_by: str,
        before_value: Optional[Dict[str, Any]] = None,
        after_value: Optional[Dict[str, Any]] = None,
        reason: Optional[str] = None,
        performed_by_user_id: Optional[str] = None,
    ) -> str:
        """Insert an audit log entry and return its inserted id."""
        doc = {
            "entityType": entity_type,
            "entityId": entity_id,
            "action": action,
            "performedBy": performed_by,
            "performedByUserId": (
                ObjectId(performed_by_user_id)
                if performed_by_user_id and ObjectId.is_valid(performed_by_user_id)
                else None
            ),
            "beforeValue": before_value,
            "afterValue": after_value,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc),
        }
        result = await self.db.asset_audit_logs.insert_one(doc)
        return str(result.inserted_id)

    async def get_audit_trail(self, entity_id: str) -> list[Dict[str, Any]]:
        """Return all audit log entries for a given entity id."""
        cursor = self.db.asset_audit_logs.find(
            {"entityId": entity_id}
        ).sort("timestamp", 1)
        logs = []
        async for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("performedByUserId"):
                doc["performedByUserId"] = str(doc["performedByUserId"])
            logs.append(doc)
        return logs
