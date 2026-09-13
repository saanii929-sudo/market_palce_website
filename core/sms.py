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


def get_sms_backend() -> BaseSMSBackend:
    backend_path = getattr(settings, "SMS_BACKEND", "core.sms.ConsoleSMSBackend")
    backend_class = import_string(backend_path)
    return backend_class()
