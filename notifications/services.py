from django.db import transaction

from .tasks import create_notification


def notify(user, type: str, title: str, body: str = "") -> None:
    """Queues the Celery task only after the enclosing transaction commits -
    callers are typically inside one (order placement, application review),
    and a real async worker could otherwise pick up the task before the
    triggering row is actually visible in the database."""
    transaction.on_commit(lambda: create_notification.delay(user.id, type, title, body))
