import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from ..models import OrderItem, Payment, ReturnRequest, ReturnRequestItem, SellerOrder
from ..notifications import notify_return_requested, notify_return_resolved


class ReturnError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _delivered_at(seller_order: SellerOrder):
    event = seller_order.status_history.filter(status=SellerOrder.Status.DELIVERED).order_by("-created_at").first()
    return event.created_at if event else None


def eligible_order_items(seller_order: SellerOrder) -> list[dict]:
    if seller_order.status != SellerOrder.Status.DELIVERED:
        return []

    delivered_at = _delivered_at(seller_order)
    if delivered_at is None:
        return []

    now = timezone.now()
    results = []
    for item in seller_order.items.select_related("product").all():
        if not item.product.is_returnable:
            continue
        deadline = delivered_at + datetime.timedelta(days=item.product.return_window_days)
        if now > deadline:
            continue

        already_requested = ReturnRequestItem.objects.filter(
            order_item=item,
            return_request__status__in=[ReturnRequest.Status.REQUESTED, ReturnRequest.Status.APPROVED],
        ).aggregate(t=Sum("qty"))["t"] or 0
        remaining = item.qty - already_requested
        if remaining > 0:
            results.append({"item": item, "remaining": remaining, "deadline": deadline})

    return results


@transaction.atomic
def request_return(*, seller_order: SellerOrder, user, lines: list[dict], reason: str) -> ReturnRequest:
    if seller_order.order.user_id != user.id:
        raise ReturnError("This isn't your order.")
    if not reason.strip():
        raise ReturnError("Please tell us why you're returning this.")

    eligible = {e["item"].id: e["remaining"] for e in eligible_order_items(seller_order)}
    if not eligible:
        raise ReturnError("This order isn't eligible for a return right now.")

    return_request = ReturnRequest.objects.create(seller_order=seller_order, user=user, reason=reason.strip())
    any_line = False
    for line in lines:
        qty = int(line.get("qty") or 0)
        try:
            order_item_id = int(line["order_item_id"])
        except (KeyError, TypeError, ValueError):
            continue
        if qty <= 0 or order_item_id not in eligible:
            continue
        if qty > eligible[order_item_id]:
            raise ReturnError("You can't return more than you bought.")

        order_item = OrderItem.objects.get(id=order_item_id, seller_order=seller_order)
        ReturnRequestItem.objects.create(return_request=return_request, order_item=order_item, qty=qty)
        any_line = True

    if not any_line:
        return_request.delete()
        raise ReturnError("Select at least one item to return.")

    notify_return_requested(return_request)
    return return_request


@transaction.atomic
def resolve_return_request(*, return_request: ReturnRequest, action: str, seller_note: str = "") -> ReturnRequest:
    if return_request.status != ReturnRequest.Status.REQUESTED:
        raise ReturnError("This return request has already been resolved.")

    if action == "approve":
        refund_amount = Decimal("0.00")
        for return_item in return_request.items.select_related("order_item__product"):
            product = return_item.order_item.product
            product.stock_qty += return_item.qty
            product.sold_count = max(0, product.sold_count - return_item.qty)
            product.save(update_fields=["stock_qty", "sold_count"])
            refund_amount += return_item.order_item.unit_price * return_item.qty

        return_request.status = ReturnRequest.Status.APPROVED
        return_request.refund_amount = refund_amount
        return_request.resolved_at = timezone.now()
        return_request.seller_note = seller_note
        return_request.save(update_fields=["status", "refund_amount", "resolved_at", "seller_note"])

        payment = return_request.order.payments.filter(status=Payment.Status.SUCCESS).order_by("-created_at").first()
        if payment:
            payment.status = Payment.Status.REFUNDED
            payment.save(update_fields=["status"])
    elif action == "reject":
        return_request.status = ReturnRequest.Status.REJECTED
        return_request.resolved_at = timezone.now()
        return_request.seller_note = seller_note
        return_request.save(update_fields=["status", "resolved_at", "seller_note"])
    else:
        raise ReturnError("Unknown action.")

    notify_return_resolved(return_request)
    return return_request
