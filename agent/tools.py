"""Real functions against the mock order store (ARCHITECTURE.md §3.3, §8).
`data/mock_orders.json` is explicitly a simulated backend — not connected
to any real order system, disclosed as such per the README/Dataset Card.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from app.config import MOCK_ORDERS_PATH

# From data/corpus/delivery.md's "Available Delivery Options" / "Delivery
# Time Policy" sections — read directly from the corpus, not invented.
_SHIPPING_ETA = {
    "standard": "3-5 business days",
    "expedited": "1-2 business days",
    "overnight": "next-day delivery",
    "in_store_pickup": None,  # picked up in person, no shipping ETA
}

# From data/corpus/cancel.md's "Cancellation Fee Policy" section.
_CANCEL_WITHIN_FREE_MINUTES = 60
_RESTOCKING_FEE_RATE = 0.10


@dataclass(frozen=True)
class CancelResult:
    outcome: str  # not_found | already_cancelled | free_no_fee | free_pending | fee_applied | unavailable_after_delivery
    order_id: str
    message: str
    fee_amount: Optional[float] = None


@dataclass(frozen=True)
class TrackResult:
    outcome: str  # not_found | found
    order_id: str
    status: Optional[str] = None
    eta: Optional[str] = None
    message: str = ""


def _load_orders() -> list:
    with open(MOCK_ORDERS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_order_id(order_id: str) -> str:
    """So "1002", "ord-1002", and "ORD-1002" all match the same order."""
    return order_id.strip().upper().removeprefix("ORD-").lstrip("-")


def _find_order(order_id: str) -> Optional[dict]:
    target = _normalize_order_id(order_id)
    for order in _load_orders():
        if _normalize_order_id(order["order_id"]) == target:
            return order
    return None


def cancel_order(order_id: str) -> CancelResult:
    order = _find_order(order_id)
    if order is None:
        return CancelResult(outcome="not_found", order_id=order_id, message=f"No order found with ID {order_id}.")

    order_id = order["order_id"]
    status = order["status"]

    if status == "cancelled":
        return CancelResult(
            outcome="already_cancelled", order_id=order_id, message="This order has already been cancelled."
        )

    if status == "delivered":
        return CancelResult(
            outcome="unavailable_after_delivery",
            order_id=order_id,
            message=(
                "This order has already been delivered, so it can't be cancelled through this "
                "process. Please use our 30-day return process instead."
            ),
        )

    if status == "shipped":
        fee = round(order["price"] * _RESTOCKING_FEE_RATE, 2)
        return CancelResult(
            outcome="fee_applied",
            order_id=order_id,
            fee_amount=fee,
            message=(
                f"This order has already shipped, so a 10% restocking fee of ${fee:.2f} "
                "applies, deducted from your refund."
            ),
        )

    if status == "placed":
        # A static elapsed-minutes fixture, not an absolute timestamp — this
        # tier boundary is always accurate regardless of when the demo runs.
        minutes_ago = order.get("placed_minutes_ago", 0)
        if minutes_ago <= _CANCEL_WITHIN_FREE_MINUTES:
            return CancelResult(
                outcome="free_no_fee",
                order_id=order_id,
                message="Your order was placed within the last hour, so it's been cancelled for free with no fee.",
            )
        return CancelResult(
            outcome="free_pending",
            order_id=order_id,
            message=(
                "Your order hasn't shipped yet, so it's been cancelled for free. "
                "This will be processed within 1 business day."
            ),
        )

    return CancelResult(
        outcome="not_found",
        order_id=order_id,
        message=f"Order {order_id} is in an unrecognized state and could not be processed.",
    )


def track_order(order_id: str) -> TrackResult:
    order = _find_order(order_id)
    if order is None:
        return TrackResult(outcome="not_found", order_id=order_id, message=f"No order found with ID {order_id}.")

    order_id = order["order_id"]
    status = order["status"]

    if status == "delivered":
        return TrackResult(
            outcome="found", order_id=order_id, status=status, message="This order has already been delivered."
        )

    if status == "cancelled":
        return TrackResult(
            outcome="found",
            order_id=order_id,
            status=status,
            message="This order was cancelled and is no longer being shipped.",
        )

    eta = _SHIPPING_ETA.get(order.get("shipping_method"))
    if eta is None:
        message = (
            "This order is set for in-store pickup — it'll be ready for you to collect "
            "at your local store rather than shipped."
        )
    else:
        message = f"This order is {status}. Estimated delivery: {eta} via {order['shipping_method']} shipping."

    return TrackResult(outcome="found", order_id=order_id, status=status, eta=eta, message=message)
