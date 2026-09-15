"""Seller application notifications, now backed by the real notifications
app (Phase 7)."""


def notify_application_reviewed(application) -> None:
    from notifications.services import notify

    if application.status == "approved":
        title = "Your seller application was approved!"
        body = "Congratulations - you're now a seller. Start listing products from your seller dashboard."
    else:
        title = "Your seller application was reviewed"
        body = application.reviewer_note or "Your application was not approved this time."

    notify(application.user, "system", title, body)


def notify_payout_requested(payout) -> None:
    from notifications.services import notify

    seller_user = payout.seller.user
    if seller_user is None:
        return
    notify(
        seller_user, "system", "Withdrawal request received",
        f"We've received your request to withdraw GH₵{payout.amount}. We'll notify you once it's reviewed.",
    )


def notify_payout_resolved(payout) -> None:
    from notifications.services import notify

    seller_user = payout.seller.user
    if seller_user is None:
        return

    messages_by_status = {
        "scheduled": f"Your withdrawal of GH₵{payout.amount} was approved and is scheduled for {payout.payout_date}.",
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
    notify(seller_user, "system", title_by_status[payout.status], body)
