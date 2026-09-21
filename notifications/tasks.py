from celery import shared_task


@shared_task
def create_notification(user_id: int, type: str, title: str, body: str = "") -> int:
    from .models import Notification
    from .push import send_push_to_users

    notification = Notification.objects.create(user_id=user_id, type=type, title=title, body=body)
    send_push_to_users([user_id], title=title, body=body, data={"type": type, "notification_id": notification.id})
    return notification.id


@shared_task
def send_broadcast(broadcast_id: int) -> int:
    """Claims the broadcast (sets sent_at) first, then fans it out - so a
    beat tick that races a manual send, or a retry, can't double-send."""
    from django.db import transaction
    from django.utils import timezone

    from accounts.models import User

    from .models import Broadcast, Notification

    with transaction.atomic():
        broadcast = Broadcast.objects.select_for_update().filter(id=broadcast_id, sent_at__isnull=True).first()
        if broadcast is None:
            return 0
        broadcast.sent_at = timezone.now()
        broadcast.save(update_fields=["sent_at"])

    if broadcast.audience == Broadcast.Audience.CUSTOMERS:
        user_ids = list(User.objects.filter(role=User.Role.CUSTOMER, is_active=True).values_list("id", flat=True))
    elif broadcast.audience == Broadcast.Audience.SELLERS:
        user_ids = list(User.objects.filter(role=User.Role.SELLER, is_active=True).values_list("id", flat=True))
    else:
        user_ids = list(User.objects.filter(is_active=True).values_list("id", flat=True))

    notifications = [
        Notification(user_id=user_id, type=Notification.Type.SYSTEM, title=broadcast.title, body=broadcast.body)
        for user_id in user_ids
    ]
    created = Notification.objects.bulk_create(notifications, batch_size=500)

    from .push import send_push_to_users

    send_push_to_users(
        user_ids, title=broadcast.title, body=broadcast.body,
        data={"type": "broadcast", "broadcast_id": broadcast.id},
    )
    return len(created)


@shared_task
def dispatch_scheduled_broadcasts() -> int:
    """Celery beat task - picks up broadcasts whose scheduled_for has
    arrived and haven't been sent yet, and fires each one off."""
    from django.utils import timezone

    from .models import Broadcast

    due_ids = list(
        Broadcast.objects.filter(scheduled_for__lte=timezone.now(), sent_at__isnull=True).values_list("id", flat=True)
    )
    for broadcast_id in due_ids:
        send_broadcast.delay(broadcast_id)
    return len(due_ids)
