from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple


class DeductionCalculator:
    """
    Pure utility class for computing deduction amounts.
    No DB access — all inputs are plain dicts/values.
    """

    @staticmethod
    def compute_deduction_per_cycle(cost_price: float, total_cycles: int) -> float:
        """floor(costPrice / totalCycles)"""
        if total_cycles <= 0:
            raise ValueError("total_cycles must be >= 1")
        return math.floor(cost_price / total_cycles)

    @staticmethod
    def sort_deductions(
        carry_forwards: List[Dict[str, Any]],
        self_issuances: List[Dict[str, Any]],
        vendor_issuances: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """
        Priority ordering:
          P0  – SELF carry-forwards (oldest weekCycle first)
          P1a – SELF active issuances (oldest issuedAt first – FIFO)
          P1b – VENDOR active issuances (oldest issuedAt first – FIFO)
        Returns a flat ordered list of 'deduction task' dicts with a
        'priority' key added for traceability.
        """
        ordered: List[Dict[str, Any]] = []

        # P0: carry-forwards (SELF only)
        for cf in sorted(carry_forwards, key=lambda x: x.get("weekCycle", "")):
            ordered.append({**cf, "priority": "P0", "taskType": "carry_forward"})

        # P1a: SELF issuances – FIFO
        for iss in sorted(self_issuances, key=lambda x: x.get("issuedAt", "")):
            ordered.append({**iss, "priority": "P1a", "taskType": "issuance"})

        # P1b: VENDOR issuances – FIFO
        for iss in sorted(vendor_issuances, key=lambda x: x.get("issuedAt", "")):
            ordered.append({**iss, "priority": "P1b", "taskType": "issuance"})

        return ordered

    @staticmethod
    def apply_deductions(
        ordered_tasks: List[Dict[str, Any]],
        available_earnings: float,
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Walk through ordered tasks and apply deductions until earnings
        are exhausted.

        Returns:
            results       – list of dicts with keys:
                              task, amount_due, amount_deducted, shortfall,
                              carried_forward, status
            final_payout  – remaining earnings (always >= 0)
        """
        assert available_earnings >= 0, "Available earnings cannot be negative"
        remaining = available_earnings
        results: List[Dict[str, Any]] = []

        for task in ordered_tasks:
            task_type = task.get("taskType")

            if task_type == "carry_forward":
                amount_due = task.get("outstandingCarryForward", 0.0)
            else:
                amount_due = task.get("deductionPerCycle", 0.0)

            if amount_due <= 0:
                continue

            if remaining >= amount_due:
                deducted = amount_due
                shortfall = 0.0
                carried_forward = False
                status = "deducted"
            elif remaining > 0:
                deducted = remaining
                shortfall = amount_due - deducted
                carried_forward = True
                status = "partial"
            else:
                deducted = 0.0
                shortfall = amount_due
                carried_forward = True
                status = "skipped"

            remaining = max(0.0, remaining - deducted)

            results.append(
                {
                    "task": task,
                    "amount_due": amount_due,
                    "amount_deducted": deducted,
                    "shortfall": shortfall,
                    "carried_forward": carried_forward,
                    "status": status,
                }
            )

        final_payout = remaining
        assert final_payout >= 0, "Final payout cannot be negative"
        return results, final_payout
