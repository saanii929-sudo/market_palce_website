import datetime
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import OrderItem, Payment, RefundRequest, RefundStatusHistory, SellerOrder
from ..notifications import notify_refund_requested
from .payment_gateway import PaymentGatewayError, get_gateway

REFUND_WINDOW_DAYS = 30


class RefundError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _delivered_at(seller_order: SellerOrder):
    event = seller_order.status_history.filter(status=SellerOrder.Status.DELIVERED).order_by("-created_at").first()
    return event.created_at if event else None


def is_refund_eligible(order_item: OrderItem) -> tuple[bool, str]:
    seller_order = order_item.seller_order
    if seller_order.status != SellerOrder.Status.DELIVERED:
        return False, "This item hasn't been delivered yet."

    delivered_at = _delivered_at(seller_order)
    if delivered_at is None:
        return False, "This item hasn't been delivered yet."

    deadline = delivered_at + datetime.timedelta(days=REFUND_WINDOW_DAYS)
    if timezone.now() > deadline:
        return False, f"The {REFUND_WINDOW_DAYS}-day refund window for this item has passed."

    return True, ""


def _is_seller_for(user, refund_request: RefundRequest) -> bool:
    seller = refund_request.order_item.seller_order.seller
    return seller.user_id is not None and seller.user_id == user.id


def _original_payment(refund_request: RefundRequest) -> Payment | None:
    order = refund_request.order_item.seller_order.order
    return order.payments.filter(status=Payment.Status.SUCCESS).order_by("-created_at").first()


@transaction.atomic
def request_refund(
    *,
    order_item: OrderItem,
    user,
    reason: str,
    reason_detail: str = "",
    photos: list | None = None,
    refund_type: str = RefundRequest.RefundType.REFUND,
) -> RefundRequest:
    if order_item.seller_order.order.user_id != user.id:
        raise RefundError("This isn't your order.")
    if reason not in RefundRequest.Reason.values:
        raise RefundError("Please select a valid reason.")
    if refund_type not in RefundRequest.RefundType.values:
        raise RefundError("Please select a valid refund type.")

    eligible, error = is_refund_eligible(order_item)
    if not eligible:
        raise RefundError(error)

    has_active_request = (
        RefundRequest.objects.filter(order_item=order_item).exclude(status=RefundRequest.Status.REJECTED).exists()
    )
    if has_active_request:
        raise RefundError("A refund request for this item is already in progress or resolved.")

    refund_request = RefundRequest.objects.create(
        order_item=order_item,
        requested_by=user,
        reason=reason,
        reason_detail=reason_detail,
        photos=photos or [],
        refund_type=refund_type,
        refund_amount=order_item.line_total,
    )
    RefundStatusHistory.objects.create(
        refund_request=refund_request, status=RefundRequest.Status.REQUESTED, actor=user
    )

    notify_refund_requested(refund_request)
    return refund_request


@transaction.atomic
def advance_refund_request(
    *,
    refund_request: RefundRequest,
    new_status: str,
    actor,
    note: str = "",
    refund_amount: Decimal | None = None,
) -> RefundRequest:
    refund_request = RefundRequest.objects.select_for_update().get(pk=refund_request.pk)

    if not refund_request.can_transition_to(new_status):
        raise RefundError(f"Can't move a {refund_request.get_status_display()} refund request to that status.")

    if refund_request.is_escalated:
        if not (actor.is_staff or actor.is_superuser):
            raise RefundError("This refund request has been escalated - only an admin can move it forward.")
    elif not (_is_seller_for(actor, refund_request) or actor.is_staff or actor.is_superuser):
        raise RefundError("Only the seller can review this refund request.")

    if new_status == RefundRequest.Status.APPROVED and refund_amount is not None:
        refund_request.refund_amount = refund_amount
        refund_request.save(update_fields=["refund_amount"])

    if new_status == RefundRequest.Status.REFUNDED:
        payment = _original_payment(refund_request)
        if payment is None:
            raise RefundError("No successful payment found to refund.")
        try:
            get_gateway(payment.gateway).refund(payment, refund_request.refund_amount)
        except PaymentGatewayError as exc:
            raise RefundError(str(exc)) from exc

        payment.status = Payment.Status.REFUNDED
        payment.save(update_fields=["status"])

    refund_request.transition_to(new_status, actor=actor, note=note)
    return refund_request


@transaction.atomic
def escalate_refund_request(*, refund_request: RefundRequest, user, reason: str = ""):
    from disputes.models import Dispute
    from disputes.services import DisputeError, create_dispute

    if refund_request.status not in (RefundRequest.Status.UNDER_REVIEW, RefundRequest.Status.REJECTED):
        raise RefundError("Only a request that's under review or was rejected can be escalated.")
    if refund_request.is_escalated:
        raise RefundError("This refund request has already been escalated.")

    is_buyer = refund_request.requested_by_id == user.id
    is_seller = _is_seller_for(user, refund_request)
    if not (is_buyer or is_seller or user.is_staff or user.is_superuser):
        raise RefundError("You can't escalate this refund request.")

    seller_user = refund_request.order_item.seller_order.seller.user
    against = seller_user if is_buyer else refund_request.requested_by

    try:
        return create_dispute(
            order=refund_request.order_item.seller_order.order,
            raised_by=user,
            category=Dispute.Category.REFUND_DISAGREEMENT,
            description=reason,
            refund_request=refund_request,
            against=against,
        )
    except DisputeError as exc:
        raise RefundError(exc.message) from exc
