import base64
import hashlib
import hmac
import logging
from decimal import Decimal

from django.conf import settings
from django.utils.module_loading import import_string

from ..models import generate_payment_reference

logger = logging.getLogger(__name__)


class PaymentGatewayError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


# PaymentMethod codes that pay online through Hubtel's hosted checkout (card
# and mobile money are both handled on Hubtel's own payment page) - these
# use the deferred, order-created-only-on-payment-success flow in
# orders.services.hubtel_checkout instead of the immediate place_order() path.
HUBTEL_PAYMENT_METHOD_CODES = {"card", "mobile_money", "hubtel"}


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


class HubtelGateway(BasePaymentGateway):
    """Hubtel's hosted Checkout API. Unlike the other gateways here, the
    order doesn't exist yet when checkout is initiated - see
    orders.services.hubtel_checkout for the deferred create-on-payment-success
    flow that drives this class's initiate_checkout/check_status directly
    (its initiate()/parse_webhook_event() satisfy BasePaymentGateway only for
    the webhook dispatch path in views.PaymentWebhookView)."""

    CHECKOUT_URL = "https://payproxyapi.hubtel.com/items/initiate"
    STATUS_URL_TEMPLATE = "https://api-txnstatus.hubtel.com/transactions/{merchant}/status"

    def _auth_header(self) -> dict:
        token = base64.b64encode(f"{settings.HUBTEL_API_ID}:{settings.HUBTEL_API_KEY}".encode()).decode()
        return {"Authorization": f"Basic {token}"}

    def initiate_checkout(
        self, *, reference: str, amount: Decimal, description: str, callback_url: str, return_url: str, cancellation_url: str
    ) -> dict:
        import requests

        if not (settings.HUBTEL_API_ID and settings.HUBTEL_API_KEY and settings.HUBTEL_MERCHANT_ACCOUNT):
            raise PaymentGatewayError("Hubtel is not configured.")

        try:
            response = requests.post(
                self.CHECKOUT_URL,
                headers=self._auth_header(),
                json={
                    "totalAmount": float(amount),
                    "description": description,
                    "callbackUrl": callback_url,
                    "returnUrl": return_url,
                    "cancellationUrl": cancellation_url,
                    "merchantAccountNumber": settings.HUBTEL_MERCHANT_ACCOUNT,
                    "clientReference": reference,
                },
                timeout=15,
            )
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise PaymentGatewayError("Couldn't reach Hubtel to start checkout. Please try again.") from exc

        data = response.json()
        if data.get("responseCode") != "0000":
            raise PaymentGatewayError(data.get("message") or "Hubtel checkout initiation failed.")

        checkout_data = data.get("data", {})
        return {"reference": reference, "authorization_url": checkout_data.get("checkoutUrl")}

    def check_status(self, reference: str) -> dict:
        """Returns {"status": "success" | "failed" | "pending", "raw_status": str}."""
        import requests

        url = self.STATUS_URL_TEMPLATE.format(merchant=settings.HUBTEL_MERCHANT_ACCOUNT)
        try:
            response = requests.get(
                url, headers=self._auth_header(), params={"clientReference": reference}, timeout=15
            )
            if response.status_code == 404:
                # Hubtel hasn't indexed this transaction yet (e.g. the status
                # is being polled moments after checkout was initiated) -
                # not a real failure, just "check back shortly".
                return {"status": "pending", "raw_status": "not_found"}
            response.raise_for_status()
        except requests.exceptions.RequestException as exc:
            raise PaymentGatewayError("Couldn't reach Hubtel to check payment status. Please try again.") from exc

        data = response.json()
        if data.get("responseCode") != "0000":
            raise PaymentGatewayError(data.get("message") or "Hubtel status check failed.")

        payload = data.get("data", {})
        raw_status = payload.get("status", "")
        if raw_status == "Paid":
            status = "success"
        elif raw_status in ("Unpaid", "Failed"):
            status = "failed"
        else:
            status = "pending"
        return {"status": status, "raw_status": raw_status}

    def initiate(self, order) -> dict:
        raise PaymentGatewayError("Hubtel checkout must be started via hubtel_checkout.start_hubtel_checkout, not initiate().")

    def verify_webhook_signature(self, request) -> bool:
        # Hubtel's callback isn't cryptographically signed, so the webhook
        # handler always re-confirms via check_status() before trusting it.
        return True

    def parse_webhook_event(self, request) -> dict:
        data = request.data.get("Data") or request.data
        reference = data.get("ClientReference") or request.data.get("clientReference")
        raw_status = str(data.get("Status") or "").lower()
        status = "success" if raw_status in ("success", "paid") else "failed"
        return {"reference": reference, "status": status}


DEFAULT_GATEWAY_BACKENDS = {
    "paystack": "orders.services.payment_gateway.MockPaymentGateway",
    "flutterwave": "orders.services.payment_gateway.MockPaymentGateway",
    "hubtel": "orders.services.payment_gateway.HubtelGateway",
    "cash_on_delivery": "orders.services.payment_gateway.MockPaymentGateway",
}


def get_gateway(name: str) -> BasePaymentGateway:
    backends = getattr(settings, "PAYMENT_GATEWAY_BACKENDS", DEFAULT_GATEWAY_BACKENDS)
    try:
        backend_path = backends[name]
    except KeyError:
        raise PaymentGatewayError(f"Unknown payment gateway: {name}")
    return import_string(backend_path)()
