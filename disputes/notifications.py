def notify_dispute_created(dispute) -> None:
    from notifications.services import notify

    notify(
        dispute.raised_by, "order_update", "Dispute opened",
        f"Your dispute for order {dispute.order.order_number} has been opened. An admin will review it shortly.",
    )
    if dispute.against_id:
        notify(
            dispute.against, "order_update", "A dispute was opened against you",
            f"A dispute was opened on order {dispute.order.order_number}. An admin will review it shortly.",
        )


def notify_dispute_message(dispute_message) -> None:
    from notifications.services import notify

    from accounts.models import User

    dispute = dispute_message.dispute
    recipient_ids = {dispute.raised_by_id, dispute.against_id, dispute.assigned_admin_id} - {None, dispute_message.sender_id}

    for user in User.objects.filter(id__in=recipient_ids):
        notify(
            user, "order_update", "New message on your dispute",
            f"There's a new message on the dispute for order {dispute.order.order_number}.",
        )


def notify_dispute_resolved(dispute) -> None:
    from notifications.services import notify

    status_titles = {
        "resolved_buyer_favor": "Dispute resolved in your favor",
        "resolved_seller_favor": "Dispute resolved",
        "resolved_partial": "Dispute resolved (partial)",
    }
    order_number = dispute.order.order_number

    notify(
        dispute.raised_by, "order_update",
        status_titles.get(dispute.status, "Dispute resolved"),
        dispute.resolution_note or f"Your dispute for order {order_number} has been resolved.",
    )
    if dispute.against_id:
        notify(
            dispute.against, "order_update", "Dispute resolved",
            dispute.resolution_note or f"The dispute for order {order_number} has been resolved.",
        )
