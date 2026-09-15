"""Pluggable SMS backend so OTP delivery can swap providers via settings.

Set SMS_BACKEND in settings to a dotted path, e.g.
"core.sms.ConsoleSMSBackend" (dev) or a future
"core.sms.AfricasTalkingSMSBackend" / "core.sms.TwilioSMSBackend".
"""
import logging

from django.conf import settings
from django.utils.module_loading import import_string

logger = logging.getLogger(__name__)


class BaseSMSBackend:
    def send(self, to: str, message: str) -> None:
        raise NotImplementedError


class ConsoleSMSBackend(BaseSMSBackend):
    """Dev backend: logs the message instead of sending it."""

    def send(self, to: str, message: str) -> None:
        logger.info("[SMS to %s] %s", to, message)


class AfricasTalkingSMSBackend(BaseSMSBackend):
    """Stub for production use. Requires AFRICASTALKING_USERNAME/API_KEY settings
    and the `africastalking` package to be installed."""

    def send(self, to: str, message: str) -> None:
        import africastalking

        africastalking.initialize(
            settings.AFRICASTALKING_USERNAME, settings.AFRICASTALKING_API_KEY
        )
        sms = africastalking.SMS
        sms.send(message, [to])


class SMSDeliveryError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class MNotifySMSBackend(BaseSMSBackend):
    """Sends SMS via mNotify (https://mnotify.com). Requires MNOTIFY_API_KEY;
    MNOTIFY_SENDER_ID must be a sender ID already approved on the mNotify
    dashboard, or their API will reject the request."""

    API_URL = "https://api.mnotify.com/api/sms/quick"

    def send(self, to: str, message: str) -> None:
        import requests

        api_key = getattr(settings, "MNOTIFY_API_KEY", "")
        if not api_key:
            raise SMSDeliveryError("MNOTIFY_API_KEY is not configured.")

        response = requests.post(
            self.API_URL,
            params={"key": api_key},
            json={
                "recipient": [self._to_local_ghana_format(to)],
                "sender": getattr(settings, "MNOTIFY_SENDER_ID", "SportShop"),
                "message": message,
                "is_schedule": False,
                "schedule_date": "",
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "success":
            raise SMSDeliveryError(data.get("message") or f"mNotify error (code {data.get('code', 'unknown')})")

    @staticmethod
    def _to_local_ghana_format(phone: str) -> str:
        """mNotify expects recipients in local Ghana format (0XXXXXXXXX), not
        the +233 E.164 format phone numbers are stored in."""
        cleaned = "".join(ch for ch in phone if ch not in " -()")
        if cleaned.startswith("+233"):
            return "0" + cleaned[4:]
        if cleaned.startswith("233"):
            return "0" + cleaned[3:]
        if not cleaned.startswith("0"):
            return "0" + cleaned
        return cleaned


def get_sms_backend() -> BaseSMSBackend:
    backend_path = getattr(settings, "SMS_BACKEND", "core.sms.ConsoleSMSBackend")
    backend_class = import_string(backend_path)
    return backend_class()
