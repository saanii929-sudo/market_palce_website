from django.db import transaction
from django.utils import timezone

from .models import Dispute, DisputeMessage
from .notifications import notify_dispute_created, notify_dispute_message, notify_dispute_resolved


class DisputeError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _is_seller_on_order(user, order) -> bool:
    seller = getattr(user, "seller_profile", None)
    return seller is not None and order.seller_orders.filter(seller=seller).exists()


def _is_participant(user, dispute: Dispute) -> bool:
    return (
        user.id == dispute.raised_by_id
        or (dispute.against_id is not None and user.id == dispute.against_id)
        or user.is_staff
        or user.is_superuser
    )


@transaction.atomic
def create_dispute(*, order, raised_by, category: str, description: str = "", evidence: list | None = None,
                    refund_request=None, against=None) -> Dispute:
    is_buyer = order.user_id == raised_by.id
    if not (is_buyer or _is_seller_on_order(raised_by, order) or raised_by.is_staff or raised_by.is_superuser):
        raise DisputeError("You're not a participant in this order.")

    if category not in Dispute.Category.values:
        raise DisputeError("Please select a valid category.")

    if refund_request is not None and refund_request.order_item.seller_order.order_id != order.id:
        raise DisputeError("That refund request doesn't belong to this order.")

    dispute = Dispute.objects.create(
        order=order, refund_request=refund_request, raised_by=raised_by, against=against,
        category=category, description=description, evidence=evidence or [],
    )
    notify_dispute_created(dispute)
    return dispute


@transaction.atomic
def add_message(*, dispute: Dispute, sender, message: str = "", attachments: list | None = None) -> DisputeMessage:
    if dispute.is_resolved:
        raise DisputeError("This dispute has already been resolved.")
    if not _is_participant(sender, dispute):
        raise DisputeError("You're not a participant in this dispute.")
    if not message.strip() and not attachments:
        raise DisputeError("Message can't be empty.")

    dispute_message = DisputeMessage.objects.create(
        dispute=dispute, sender=sender, message=message, attachments=attachments or []
    )
    notify_dispute_message(dispute_message)
    return dispute_message


@transaction.atomic
def resolve_dispute(*, dispute: Dispute, admin_user, new_status: str, resolution_note: str = "") -> Dispute:
    from orders.models import RefundRequest
    from orders.services.refunds import RefundError, advance_refund_request

    if dispute.is_resolved:
        raise DisputeError("This dispute has already been resolved.")
    if new_status not in Dispute.RESOLVED_STATUSES:
        raise DisputeError("Resolution must be one of the resolved statuses.")

    dispute.status = new_status
    dispute.resolution_note = resolution_note
    dispute.resolved_at = timezone.now()
    if dispute.assigned_admin_id is None:
        dispute.assigned_admin = admin_user
    dispute.save(update_fields=["status", "resolution_note", "resolved_at", "assigned_admin"])

    if new_status == Dispute.Status.RESOLVED_BUYER_FAVOR and dispute.refund_request_id:
        refund_request = dispute.refund_request
        if refund_request.status in (RefundRequest.Status.REQUESTED, RefundRequest.Status.UNDER_REVIEW, RefundRequest.Status.REJECTED):
            refund_request.admin_override_to(
                RefundRequest.Status.APPROVED, actor=admin_user,
                note="Dispute resolved in buyer's favor.",
            )
        if refund_request.status == RefundRequest.Status.APPROVED:
            try:
                advance_refund_request(
                    refund_request=refund_request, new_status=RefundRequest.Status.REFUNDED, actor=admin_user,
                    note="Refunded per dispute resolution.",
                )
            except RefundError as exc:
                dispute.resolution_note = f"{resolution_note} (refund could not be completed automatically: {exc.message})".strip()
                dispute.save(update_fields=["resolution_note"])

    notify_dispute_resolved(dispute)
    return dispute
