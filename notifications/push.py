"""Real FCM push delivery for the Flutter app, on top of the existing
in-app Notification row. Every call here is best-effort: if Firebase isn't
configured (FIREBASE_CREDENTIALS_PATH unset - the default in dev), or the
send itself fails, the in-app notification the caller already created is
unaffected. Only ever called from inside a Celery task (see
notifications.tasks) - never on the request path."""

import logging

from core.firebase import get_firebase_app

logger = logging.getLogger(__name__)

FCM_MULTICAST_LIMIT = 500


def _send_multicast(device_tokens: list, *, title: str, body: str, data: dict | None = None) -> None:
    app = get_firebase_app()
    if app is None or not device_tokens:
        return

    from firebase_admin import messaging

    string_data = {str(k): str(v) for k, v in (data or {}).items()}
    invalid_ids = []

    for start in range(0, len(device_tokens), FCM_MULTICAST_LIMIT):
        chunk = device_tokens[start:start + FCM_MULTICAST_LIMIT]
        message = messaging.MulticastMessage(
            notification=messaging.Notification(title=title, body=body),
            data=string_data,
            tokens=[device.token for device in chunk],
        )
        try:
            response = messaging.send_each_for_multicast(message, app=app)
        except Exception:
            logger.exception("Failed to send push notification batch (%d tokens)", len(chunk))
            continue

        for device, result in zip(chunk, response.responses):
            if not result.success and isinstance(result.exception, messaging.UnregisteredError):
                invalid_ids.append(device.id)

    if invalid_ids:
        from .models import DeviceToken

        DeviceToken.objects.filter(id__in=invalid_ids).update(is_active=False)


def send_push_to_user(user, *, title: str, body: str, data: dict | None = None) -> None:
    from .models import DeviceToken

    device_tokens = list(DeviceToken.objects.filter(user=user, is_active=True))
    _send_multicast(device_tokens, title=title, body=body, data=data)


def send_push_to_users(user_ids, *, title: str, body: str, data: dict | None = None) -> None:
    from .models import DeviceToken

    device_tokens = list(DeviceToken.objects.filter(user_id__in=user_ids, is_active=True))
    _send_multicast(device_tokens, title=title, body=body, data=data)
