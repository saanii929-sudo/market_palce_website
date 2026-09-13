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
