import hashlib
import hmac
import logging

from django.conf import settings
from django.utils.module_loading import import_string

from ..models import generate_payment_reference

logger = logging.getLogger(__name__)


class PaymentGatewayError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class BasePaymentGateway:
    def initiate(self, order) -> dict:
        """Returns {"reference": str, "authorization_url": str | None}."""
        raise NotImplementedError

    def verify_webhook_signature(self, request) -> bool:
        raise NotImplementedError

    def parse_webhook_event(self, request) -> dict:
        """Returns {"reference": str, "status": "success" | "failed"}."""
        raise NotImplementedError


class MockPaymentGateway(BasePaymentGateway):
    """Simulates an always-successful gateway for dev/test/cash-on-delivery."""

    def initiate(self, order) -> dict:
        return {"reference": generate_payment_reference(), "authorization_url": None}

    def verify_webhook_signature(self, request) -> bool:
        return True

    def parse_webhook_event(self, request) -> dict:
        return {"reference": request.data.get("reference"), "status": request.data.get("status", "success")}


class PaystackGateway(BasePaymentGateway):
    def initiate(self, order) -> dict:
        raise PaymentGatewayError("Paystack integration is not configured yet.")

    def verify_webhook_signature(self, request) -> bool:
        signature = request.headers.get("X-Paystack-Signature", "")
        expected = hmac.new(
            settings.PAYSTACK_SECRET_KEY.encode(), request.body, hashlib.sha512
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def parse_webhook_event(self, request) -> dict:
        data = request.data.get("data", {})
        status = "success" if data.get("status") == "success" else "failed"
        return {"reference": data.get("reference"), "status": status}


class FlutterwaveGateway(BasePaymentGateway):
    def initiate(self, order) -> dict:
        raise PaymentGatewayError("Flutterwave integration is not configured yet.")

    def verify_webhook_signature(self, request) -> bool:
        signature = request.headers.get("verif-hash", "")
        return hmac.compare_digest(signature, settings.FLUTTERWAVE_SECRET_HASH)

    def parse_webhook_event(self, request) -> dict:
        data = request.data.get("data", {})
        status = "success" if data.get("status") == "successful" else "failed"
        return {"reference": data.get("tx_ref"), "status": status}


DEFAULT_GATEWAY_BACKENDS = {
    "paystack": "orders.services.payment_gateway.MockPaymentGateway",
    "flutterwave": "orders.services.payment_gateway.MockPaymentGateway",
    "cash_on_delivery": "orders.services.payment_gateway.MockPaymentGateway",
}


def get_gateway(name: str) -> BasePaymentGateway:
    backends = getattr(settings, "PAYMENT_GATEWAY_BACKENDS", DEFAULT_GATEWAY_BACKENDS)
    try:
        backend_path = backends[name]
    except KeyError:
        raise PaymentGatewayError(f"Unknown payment gateway: {name}")
    return import_string(backend_path)()
