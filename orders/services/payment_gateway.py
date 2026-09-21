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


HUBTEL_PAYMENT_METHOD_CODES = {"card", "mobile_money", "hubtel"}


class BasePaymentGateway:
    def initiate(self, order) -> dict:
        raise NotImplementedError

    def verify_webhook_signature(self, request) -> bool:
        raise NotImplementedError

    def parse_webhook_event(self, request) -> dict:
        raise NotImplementedError

    def refund(self, payment, amount: Decimal) -> dict:
        raise NotImplementedError

    def tokenize_payout_destination(self, *, type: str, account_number: str, bank_code: str = "", account_name: str = "") -> str:
        """Returns a tokenized transfer-recipient reference for a seller
        payout destination. Raises PaymentGatewayError on failure. Never
        pass this the raw number back to the caller to store - only the
        returned reference is meant to be persisted."""
        raise NotImplementedError


class MockPaymentGateway(BasePaymentGateway):
    def initiate(self, order) -> dict:
        return {"reference": generate_payment_reference(), "authorization_url": None}

    def verify_webhook_signature(self, request) -> bool:
        return True

    def parse_webhook_event(self, request) -> dict:
        return {"reference": request.data.get("reference"), "status": request.data.get("status", "success")}

    def refund(self, payment, amount: Decimal) -> dict:
        return {"reference": f"RFD-{generate_payment_reference()}"}

    def tokenize_payout_destination(self, *, type: str, account_number: str, bank_code: str = "", account_name: str = "") -> str:
        return f"RCP-{generate_payment_reference()}"


class PaystackGateway(BasePaymentGateway):
    def initiate(self, order) -> dict:
        raise PaymentGatewayError("Paystack integration is not configured yet.")

    def refund(self, payment, amount: Decimal) -> dict:
        raise PaymentGatewayError("Paystack refunds are not configured yet.")

    def tokenize_payout_destination(self, *, type: str, account_number: str, bank_code: str = "", account_name: str = "") -> str:
        raise PaymentGatewayError("Paystack transfer-recipient tokenization is not configured yet.")

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

    def refund(self, payment, amount: Decimal) -> dict:
        raise PaymentGatewayError("Flutterwave refunds are not configured yet.")

    def tokenize_payout_destination(self, *, type: str, account_number: str, bank_code: str = "", account_name: str = "") -> str:
        raise PaymentGatewayError("Flutterwave transfer-recipient tokenization is not configured yet.")

    def verify_webhook_signature(self, request) -> bool:
        signature = request.headers.get("verif-hash", "")
        return hmac.compare_digest(signature, settings.FLUTTERWAVE_SECRET_HASH)

    def parse_webhook_event(self, request) -> dict:
        data = request.data.get("data", {})
        status = "success" if data.get("status") == "successful" else "failed"
        return {"reference": data.get("tx_ref"), "status": status}


class HubtelGateway(BasePaymentGateway):
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
        import requests

        url = self.STATUS_URL_TEMPLATE.format(merchant=settings.HUBTEL_MERCHANT_ACCOUNT)
        try:
            response = requests.get(
                url, headers=self._auth_header(), params={"clientReference": reference}, timeout=15
            )
            if response.status_code == 404:
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

    def refund(self, payment, amount: Decimal) -> dict:
        raise PaymentGatewayError("Hubtel refunds are not configured yet.")

    def verify_webhook_signature(self, request) -> bool:
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
