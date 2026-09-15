import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.tests.factories import ProductFactory

from ..models import Order, Payment
from .factories import DeliveryMethodFactory, PaymentMethodFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_checkout_summary_reflects_cart_and_delivery_fee():
    user = UserFactory(email="summary@example.com")
    delivery = DeliveryMethodFactory(price="7.50")
    product = ProductFactory(price="50.00")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 2})

    response = client.get(reverse("checkout-summary"), {"delivery_method_id": delivery.id})
    assert response.status_code == 200
    assert response.data["subtotal"] == "100.00"
    assert response.data["delivery_fee"] == "7.50"
    assert response.data["total"] == "107.50"


@pytest.mark.django_db
def test_checkout_summary_requires_authentication():
    client = APIClient()
    response = client.get(reverse("checkout-summary"))
    assert response.status_code == 401


@pytest.mark.django_db
def test_checkout_summary_without_delivery_method_has_zero_fee():
    user = UserFactory(email="nodeliv@example.com")
    product = ProductFactory(price="30.00")
    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    response = client.get(reverse("checkout-summary"))
    assert response.data["delivery_fee"] in ("0.00", 0, "0")


@pytest.mark.django_db
def test_webhook_success_marks_payment_success():
    # "other_gateway" (not "card"/"mobile_money") so this exercises the
    # generic eager-order-then-webhook-confirms mechanism - those two real
    # codes now route through the Hubtel deferred flow instead, see
    # test_hubtel_checkout.py.
    user = UserFactory(email="webhookok@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory(code="other_gateway", name="Other Gateway")
    product = ProductFactory(price="20.00", stock_qty=5)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    order_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    order = Order.objects.get(order_number=order_response.data["order_number"])
    payment = order.payments.first()

    webhook_client = APIClient()
    response = webhook_client.post(
        reverse("payment-webhook", kwargs={"gateway": "paystack"}),
        {"reference": payment.gateway_reference, "status": "success"},
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.SUCCESS
    assert order.status == Order.Status.PROCESSING


@pytest.mark.django_db
def test_webhook_failure_cancels_order():
    user = UserFactory(email="webhookfail@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory(code="other_gateway", name="Other Gateway")
    product = ProductFactory(price="20.00", stock_qty=5)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    order_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    order = Order.objects.get(order_number=order_response.data["order_number"])
    payment = order.payments.first()

    webhook_client = APIClient()
    response = webhook_client.post(
        reverse("payment-webhook", kwargs={"gateway": "paystack"}),
        {"reference": payment.gateway_reference, "status": "failed"},
    )
    assert response.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.CANCELLED


@pytest.mark.django_db
def test_webhook_unknown_reference_returns_404():
    webhook_client = APIClient()
    response = webhook_client.post(
        reverse("payment-webhook", kwargs={"gateway": "paystack"}),
        {"reference": "does-not-exist", "status": "success"},
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_webhook_unknown_gateway_returns_404():
    webhook_client = APIClient()
    response = webhook_client.post(reverse("payment-webhook", kwargs={"gateway": "unknown"}), {})
    assert response.status_code == 404


@pytest.mark.django_db
def test_webhook_is_idempotent_on_replayed_delivery():
    user = UserFactory(email="webhookreplay@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory(code="other_gateway", name="Other Gateway")
    product = ProductFactory(price="20.00", stock_qty=5)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    order_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    order = Order.objects.get(order_number=order_response.data["order_number"])
    payment = order.payments.first()

    webhook_client = APIClient()
    payload = {"reference": payment.gateway_reference, "status": "failed"}

    first = webhook_client.post(reverse("payment-webhook", kwargs={"gateway": "paystack"}), payload)
    second = webhook_client.post(reverse("payment-webhook", kwargs={"gateway": "paystack"}), payload)

    assert first.status_code == 200
    assert second.status_code == 200

    payment.refresh_from_db()
    order.refresh_from_db()
    assert payment.status == Payment.Status.FAILED
    assert order.status == Order.Status.CANCELLED
    # a replayed "failed" delivery must not raise trying to cancel an
    # already-cancelled order
    assert order.status_history.filter(status=Order.Status.CANCELLED).count() == 1
