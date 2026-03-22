from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from config import get_db
from services.settlement_engine import SettlementEngine

logger = logging.getLogger(__name__)


async def run_scheduled_settlement() -> None:
    """
    Cron-triggered weekly settlement.

    Runs every Saturday at 23:00 UTC.
    Processes the week from the previous Sunday (inclusive) to
    the current Saturday (inclusive).

    Note: rider_earnings is empty dict because earnings come from an
    external input in real usage. In production, integrate with the
    earnings/payout service here.
    """
    now = datetime.now(timezone.utc)
    # Compute week boundaries: Mon–Sun ISO week
    iso_year, iso_week, iso_weekday = now.isocalendar()

    # Week start = Monday of current ISO week
    week_start = now - timedelta(days=iso_weekday - 1)
    week_start = week_start.replace(hour=0, minute=0, second=0, microsecond=0)

    # Week end = Sunday of current ISO week
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)

    logger.info(
        "Cron: starting weekly settlement for %s to %s",
        week_start.isoformat(),
        week_end.isoformat(),
    )

    try:
        db = await get_db()
        engine = SettlementEngine(db)

        # In production, fetch rider earnings from the payouts/orders service.
        # For the scheduled cron we compute earnings from the orders collection.
        rider_earnings = await _compute_rider_earnings(db, week_start, week_end)

        result = await engine.run_weekly_settlement(
            week_start=week_start,
            week_end=week_end,
            rider_earnings=rider_earnings,
        )

        logger.info(
            "Cron: settlement complete. Week=%s riders=%d totalDeducted=%.2f errors=%d",
            result["weekCycle"],
            result["totalRidersProcessed"],
            result["totalDeducted"],
            len(result["errors"]),
        )

        if result["errors"]:
            for err in result["errors"]:
                logger.error("Cron settlement error: %s", err)

    except Exception:
        logger.exception("Cron: weekly settlement job failed")


async def _compute_rider_earnings(db, week_start: datetime, week_end: datetime) -> dict:
    """
    Aggregate rider earnings from the orders collection for a given week.

    Assumes orders have a 'riderEarnings' or 'deliveryFee' field.
    Adjust field name to match your actual schema.
    """
    pipeline = [
        {
            "$match": {
                "createdAt": {"$gte": week_start, "$lte": week_end},
                "rider": {"$exists": True, "$ne": None},
            }
        },
        {
            "$group": {
                "_id": "$rider",
                # Adjust the earnings field name to match your schema
                "totalEarnings": {"$sum": {"$ifNull": ["$riderEarnings", 0]}},
            }
        },
    ]

    earnings: dict = {}
    async for doc in db.orders.aggregate(pipeline):
        rider_id_str = str(doc["_id"])
        earnings[rider_id_str] = float(doc.get("totalEarnings", 0.0))

    return earnings


def setup_cron_jobs(scheduler: AsyncIOScheduler) -> None:
    """
    Register the weekly settlement cron job on the provided scheduler.

    Runs every Saturday at 23:00 UTC.
    """
    scheduler.add_job(
        run_scheduled_settlement,
        trigger=CronTrigger(
            day_of_week="sat",
            hour=23,
            minute=0,
            second=0,
            timezone="UTC",
        ),
        id="weekly_settlement",
        name="Weekly Asset Settlement",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow up to 1 hour late start
    )
    logger.info("Cron: weekly settlement job registered (Saturday 23:00 UTC)")
