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

# Phase B (Arabic support, see ANALYSIS.md): fixed, bounded template set —
# hardcoded Arabic variants rather than a live translation pass, since a
# translation call risks mangling a dollar figure or order ID and these
# messages carry both.
_SHIPPING_ETA_AR = {
    "standard": "3-5 أيام عمل",
    "expedited": "1-2 يوم عمل",
    "overnight": "التوصيل في اليوم التالي",
    "in_store_pickup": None,
}
_STATUS_AR = {
    "placed": "تم تقديمه",
    "shipped": "تم شحنه",
    "delivered": "تم توصيله",
    "cancelled": "ملغى",
}
_SHIPPING_METHOD_AR = {
    "standard": "عادي",
    "expedited": "سريع",
    "overnight": "ليلي",
    "in_store_pickup": "استلام من المتجر",
}

# From data/corpus/cancel.md's "Cancellation Fee Policy" section.
_CANCEL_WITHIN_FREE_MINUTES = 60
_RESTOCKING_FEE_RATE = 0.10


def _pick(language: str, en: str, ar: str) -> str:
    return ar if language == "ar" else en


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


def cancel_order(order_id: str, language: str = "en") -> CancelResult:
    order = _find_order(order_id)
    if order is None:
        return CancelResult(
            outcome="not_found", order_id=order_id,
            message=_pick(language, f"No order found with ID {order_id}.", f"لم يتم العثور على طلب برقم {order_id}."),
        )

    order_id = order["order_id"]
    status = order["status"]

    if status == "cancelled":
        return CancelResult(
            outcome="already_cancelled", order_id=order_id,
            message=_pick(language, "This order has already been cancelled.", "تم إلغاء هذا الطلب بالفعل."),
        )

    if status == "delivered":
        return CancelResult(
            outcome="unavailable_after_delivery",
            order_id=order_id,
            message=_pick(
                language,
                "This order has already been delivered, so it can't be cancelled through this "
                "process. Please use our 30-day return process instead.",
                "تم توصيل هذا الطلب بالفعل، لذا لا يمكن إلغاؤه من خلال هذه العملية. "
                "يرجى استخدام عملية الإرجاع خلال 30 يومًا بدلاً من ذلك.",
            ),
        )

    if status == "shipped":
        fee = round(order["price"] * _RESTOCKING_FEE_RATE, 2)
        return CancelResult(
            outcome="fee_applied",
            order_id=order_id,
            fee_amount=fee,
            message=_pick(
                language,
                f"This order has already shipped, so a 10% restocking fee of ${fee:.2f} "
                "applies, deducted from your refund.",
                f"تم شحن هذا الطلب بالفعل، لذا تُطبَّق رسوم إعادة تخزين بنسبة 10% تبلغ "
                f"${fee:.2f}، تُخصَم من مبلغ استردادك.",
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
                message=_pick(
                    language,
                    "Your order was placed within the last hour, so it's been cancelled for free with no fee.",
                    "تم تقديم طلبك خلال الساعة الماضية، لذا تم إلغاؤه مجانًا دون أي رسوم.",
                ),
            )
        return CancelResult(
            outcome="free_pending",
            order_id=order_id,
            message=_pick(
                language,
                "Your order hasn't shipped yet, so it's been cancelled for free. "
                "This will be processed within 1 business day.",
                "لم يتم شحن طلبك بعد، لذا تم إلغاؤه مجانًا. ستتم معالجة هذا الأمر خلال يوم عمل واحد.",
            ),
        )

    return CancelResult(
        outcome="not_found",
        order_id=order_id,
        message=_pick(
            language,
            f"Order {order_id} is in an unrecognized state and could not be processed.",
            f"الطلب {order_id} في حالة غير معروفة ولم تتم معالجته.",
        ),
    )


def track_order(order_id: str, language: str = "en") -> TrackResult:
    order = _find_order(order_id)
    if order is None:
        return TrackResult(
            outcome="not_found", order_id=order_id,
            message=_pick(language, f"No order found with ID {order_id}.", f"لم يتم العثور على طلب برقم {order_id}."),
        )

    order_id = order["order_id"]
    status = order["status"]

    if status == "delivered":
        return TrackResult(
            outcome="found", order_id=order_id, status=status,
            message=_pick(language, "This order has already been delivered.", "تم توصيل هذا الطلب بالفعل."),
        )

    if status == "cancelled":
        return TrackResult(
            outcome="found",
            order_id=order_id,
            status=status,
            message=_pick(
                language,
                "This order was cancelled and is no longer being shipped.",
                "تم إلغاء هذا الطلب ولم يعد قيد الشحن.",
            ),
        )

    eta = _SHIPPING_ETA.get(order.get("shipping_method"))
    if eta is None:
        message = _pick(
            language,
            "This order is set for in-store pickup — it'll be ready for you to collect "
            "at your local store rather than shipped.",
            "هذا الطلب مخصص للاستلام من المتجر — سيكون جاهزًا لاستلامه من متجرك المحلي بدلاً من شحنه.",
        )
    else:
        message = _pick(
            language,
            f"This order is {status}. Estimated delivery: {eta} via {order['shipping_method']} shipping.",
            f"حالة هذا الطلب: {_STATUS_AR.get(status, status)}. موعد التسليم المتوقع: "
            f"{_SHIPPING_ETA_AR.get(order.get('shipping_method'), eta)} عبر شحن "
            f"{_SHIPPING_METHOD_AR.get(order.get('shipping_method'), order['shipping_method'])}.",
        )

    return TrackResult(outcome="found", order_id=order_id, status=status, eta=eta, message=message)
