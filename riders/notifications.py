from notifications.services import notify


def notify_rider_payout_requested(payout) -> None:
    notify(
        payout.rider.user, "system", "Withdrawal request received",
        f"We've received your request to withdraw GH₵{payout.amount}. We'll notify you once it's reviewed.",
    )


def notify_rider_payout_resolved(payout) -> None:
    messages_by_status = {
        "scheduled": f"Your withdrawal of GH₵{payout.amount} was approved and is being processed.",
        "paid": f"Your withdrawal of GH₵{payout.amount} has been paid out.",
        "rejected": payout.admin_note or f"Your withdrawal request for GH₵{payout.amount} was declined.",
    }
    title_by_status = {
        "scheduled": "Withdrawal approved",
        "paid": "Withdrawal paid",
        "rejected": "Withdrawal declined",
    }
    body = messages_by_status.get(payout.status)
    if body is None:
        return
    notify(payout.rider.user, "system", title_by_status[payout.status], body)
