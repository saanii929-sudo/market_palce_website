from celery import shared_task


@shared_task
def create_notification(user_id: int, type: str, title: str, body: str = "") -> int:
    from .models import Notification

    notification = Notification.objects.create(user_id=user_id, type=type, title=title, body=body)
    return notification.id
