from unittest.mock import Mock, patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.tests.factories import ProductFactory

from ..models import Order, Payment, PendingCheckout
from .factories import DeliveryMethodFactory, PaymentMethodFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _hubtel_initiate_response(reference="HBT-TEST", checkout_url="https://pay.hubtel.com/checkout/abc"):
    return Mock(
        status_code=200,
        json=lambda: {
            "responseCode": "0000",
            "data": {"checkoutUrl": checkout_url, "checkoutId": "abc", "clientReference": reference},
        },
        raise_for_status=Mock(),
    )


def _hubtel_status_response(hubtel_status="Paid", amount="20.00"):
    return Mock(
        status_code=200,
        json=lambda: {
            "responseCode": "0000",
            "data": {"status": hubtel_status, "amount": amount, "transactionId": "TXN123"},
        },
        raise_for_status=Mock(),
    )


def _setup_checkout(settings, price="20.00", stock_qty=5):
    settings.HUBTEL_API_ID = "id"
    settings.HUBTEL_API_KEY = "key"
    settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"

    user = UserFactory()
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory(code="hubtel", name="Hubtel")
    product = ProductFactory(price=price, stock_qty=stock_qty)
    return user, address, delivery, payment_method, product


@pytest.mark.django_db
@patch("requests.post")
def test_place_order_with_hubtel_does_not_create_order_yet(mock_post, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response()

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    assert response.status_code == 201
    assert response.data["status"] == PendingCheckout.Status.PENDING
    assert response.data["checkout_url"] == "https://pay.hubtel.com/checkout/abc"
    assert Order.objects.count() == 0
    assert PendingCheckout.objects.count() == 1

    product.refresh_from_db()
    assert product.stock_qty == 5  # not decremented until payment confirms


@pytest.mark.django_db
@patch("requests.get")
@patch("requests.post")
def test_webhook_success_creates_order_and_finalizes_checkout(mock_post, mock_get, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response(reference="HBT-SUCCESS")
    mock_get.return_value = _hubtel_status_response("Paid")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 2})
    place_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    reference = place_response.data["reference"]

    webhook_client = APIClient()
    response = webhook_client.post(
        reverse("payment-webhook", kwargs={"gateway": "hubtel"}),
        {"Data": {"ClientReference": reference, "Status": "Success"}},
        format="json",
    )
    assert response.status_code == 200

    pending = PendingCheckout.objects.get(reference=reference)
    assert pending.status == PendingCheckout.Status.PAID
    assert pending.order is not None

    order = pending.order
    assert order.status == Order.Status.PROCESSING
    assert order.items.count() == 1
    assert order.items.first().qty == 2
    assert Payment.objects.filter(order=order, gateway=Payment.Gateway.HUBTEL, status=Payment.Status.SUCCESS).exists()

    product.refresh_from_db()
    assert product.stock_qty == 3
    assert product.sold_count == 2

    cart_response = client.get(reverse("cart-detail"))
    assert cart_response.data["items"] == []


@pytest.mark.django_db
@patch("requests.get")
@patch("requests.post")
def test_webhook_failure_never_creates_an_order(mock_post, mock_get, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response(reference="HBT-FAILED")
    mock_get.return_value = _hubtel_status_response("Unpaid")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    place_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    reference = place_response.data["reference"]

    webhook_client = APIClient()
    response = webhook_client.post(
        reverse("payment-webhook", kwargs={"gateway": "hubtel"}),
        {"Data": {"ClientReference": reference, "Status": "Failed"}},
        format="json",
    )
    assert response.status_code == 200

    pending = PendingCheckout.objects.get(reference=reference)
    assert pending.status == PendingCheckout.Status.FAILED
    assert pending.order is None
    assert Order.objects.count() == 0

    product.refresh_from_db()
    assert product.stock_qty == 5


@pytest.mark.django_db
@patch("requests.get")
@patch("requests.post")
def test_webhook_is_idempotent_on_replay(mock_post, mock_get, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response(reference="HBT-REPLAY")
    mock_get.return_value = _hubtel_status_response("Paid")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    place_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    reference = place_response.data["reference"]

    webhook_client = APIClient()
    payload = {"Data": {"ClientReference": reference, "Status": "Success"}}
    first = webhook_client.post(reverse("payment-webhook", kwargs={"gateway": "hubtel"}), payload, format="json")
    second = webhook_client.post(reverse("payment-webhook", kwargs={"gateway": "hubtel"}), payload, format="json")

    assert first.status_code == 200
    assert second.status_code == 200
    assert Order.objects.filter(payment_method=payment_method).count() == 1
    product.refresh_from_db()
    assert product.stock_qty == 4  # decremented only once


@pytest.mark.django_db
@patch("requests.get")
@patch("requests.post")
def test_status_poll_finalizes_when_still_pending(mock_post, mock_get, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response(reference="HBT-POLL")
    mock_get.return_value = _hubtel_status_response("Paid")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    place_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    reference = place_response.data["reference"]

    response = client.get(reverse("hubtel-checkout-status"), {"reference": reference})
    assert response.status_code == 200
    assert response.data["status"] == PendingCheckout.Status.PAID
    assert response.data["order"]["order_number"]


@pytest.mark.django_db
@patch("requests.post")
def test_insufficient_stock_at_finalize_time_fails_without_creating_order(mock_post, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings, stock_qty=1)
    mock_post.return_value = _hubtel_initiate_response()

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    place_response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    # Someone else buys the last unit while this Hubtel payment is in flight.
    product.stock_qty = 0
    product.save(update_fields=["stock_qty"])

    from ..services.hubtel_checkout import finalize_pending_checkout

    pending = PendingCheckout.objects.get(reference=place_response.data["reference"])
    result = finalize_pending_checkout(pending)

    assert result.status == PendingCheckout.Status.FAILED
    assert "out of stock" in result.failure_reason.lower()
    assert Order.objects.count() == 0


@pytest.mark.django_db
@patch("requests.post")
def test_existing_card_and_mobile_money_methods_route_through_hubtel(mock_post, settings):
    """Real checkout pages offer 'card'/'mobile_money' PaymentMethod rows
    (seeded by seed_demo), not a literal 'hubtel' one - both must trigger the
    hosted-checkout flow instead of silently completing via the mock gateway."""
    settings.HUBTEL_API_ID = "id"
    settings.HUBTEL_API_KEY = "key"
    settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
    mock_post.return_value = _hubtel_initiate_response()

    user = UserFactory()
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    product = ProductFactory(price="20.00", stock_qty=5)

    for code in ("card", "mobile_money"):
        payment_method = PaymentMethodFactory(code=code, name=code)
        client = authed_client(user)
        client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
        response = client.post(
            reverse("order-list"),
            {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
        )
        assert response.status_code == 201
        assert "checkout_url" in response.data
        assert response.data["status"] == PendingCheckout.Status.PENDING

    assert Order.objects.count() == 0


@pytest.mark.django_db
@patch("requests.post")
def test_retrying_with_same_idempotency_key_reuses_pending_checkout(mock_post, settings):
    user, address, delivery, payment_method, product = _setup_checkout(settings)
    mock_post.return_value = _hubtel_initiate_response(reference="HBT-IDEMP")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    payload = {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id}

    first = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="hubtel-key-1")
    second = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="hubtel-key-1")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.data["reference"] == second.data["reference"]
    assert PendingCheckout.objects.count() == 1
    assert mock_post.call_count == 1
