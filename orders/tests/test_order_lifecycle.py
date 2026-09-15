import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.tests.factories import ProductFactory

from ..models import Order, Shipment
from .factories import DeliveryMethodFactory, PaymentMethodFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _place_order(user, product_qty=1):
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="20.00", stock_qty=10)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": product_qty})
    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    return client, response.data["order_number"], product


@pytest.mark.django_db
def test_order_detail_and_list_scoped_to_owner():
    owner = UserFactory(email="owner2@example.com")
    other = UserFactory(email="other2@example.com")
    client, order_number, _ = _place_order(owner)

    detail = client.get(reverse("order-detail", kwargs={"order_number": order_number}))
    assert detail.status_code == 200
    assert detail.data["order_number"] == order_number

    other_client = authed_client(other)
    other_detail = other_client.get(reverse("order-detail", kwargs={"order_number": order_number}))
    assert other_detail.status_code == 404

    list_response = client.get(reverse("order-list"))
    assert list_response.data["count"] == 1


@pytest.mark.django_db
def test_order_list_filters_by_status():
    user = UserFactory(email="statusfilter@example.com")
    client, order_number, _ = _place_order(user)

    processing = client.get(reverse("order-list"), {"status": "processing"})
    assert processing.data["count"] == 1

    delivered = client.get(reverse("order-list"), {"status": "delivered"})
    assert delivered.data["count"] == 0


@pytest.mark.django_db
def test_tracking_reflects_status_history():
    user = UserFactory(email="tracker@example.com")
    client, order_number, _ = _place_order(user)

    response = client.get(reverse("order-tracking", kwargs={"order_number": order_number}))
    assert response.status_code == 200
    statuses = [h["status"] for h in response.data["status_history"]]
    assert statuses == [Order.Status.PROCESSING]

    order = Order.objects.get(order_number=order_number)
    order.transition_to(Order.Status.SHIPPED, note="Left the warehouse.")

    response = client.get(reverse("order-tracking", kwargs={"order_number": order_number}))
    statuses = [h["status"] for h in response.data["status_history"]]
    assert statuses == [Order.Status.PROCESSING, Order.Status.SHIPPED]
    assert response.data["status"] == Order.Status.SHIPPED


@pytest.mark.django_db
def test_transition_creates_and_syncs_shipment_status():
    user = UserFactory(email="shipmentsync@example.com")
    client, order_number, _ = _place_order(user)
    order = Order.objects.get(order_number=order_number)

    assert not Shipment.objects.filter(order=order).exists()

    order.transition_to(Order.Status.SHIPPED, note="Left the warehouse.")
    shipment = Shipment.objects.get(order=order)
    assert shipment.current_status == "Shipped"

    order.transition_to(Order.Status.OUT_FOR_DELIVERY)
    shipment.refresh_from_db()
    assert shipment.current_status == "Out for delivery"


@pytest.mark.django_db
def test_tracking_endpoint_surfaces_courier_and_tracking_number():
    user = UserFactory(email="trackingnumber@example.com")
    client, order_number, _ = _place_order(user)
    order = Order.objects.get(order_number=order_number)
    order.transition_to(Order.Status.SHIPPED)

    shipment = Shipment.objects.get(order=order)
    shipment.courier_name = "DHL"
    shipment.tracking_number = "DHL123456789"
    shipment.save(update_fields=["courier_name", "tracking_number"])

    response = client.get(reverse("order-tracking", kwargs={"order_number": order_number}))
    assert response.data["shipment"]["courier_name"] == "DHL"
    assert response.data["shipment"]["tracking_number"] == "DHL123456789"


@pytest.mark.django_db
def test_cancel_allowed_while_processing():
    user = UserFactory(email="cancelme@example.com")
    client, order_number, product = _place_order(user, product_qty=2)

    response = client.post(reverse("order-cancel", kwargs={"order_number": order_number}))
    assert response.status_code == 200
    assert response.data["status"] == Order.Status.CANCELLED


@pytest.mark.django_db
def test_cancel_rejected_once_shipped():
    user = UserFactory(email="noshipcancel@example.com")
    client, order_number, _ = _place_order(user)

    order = Order.objects.get(order_number=order_number)
    order.transition_to(Order.Status.SHIPPED)

    response = client.post(reverse("order-cancel", kwargs={"order_number": order_number}))
    assert response.status_code == 400
    order.refresh_from_db()
    assert order.status == Order.Status.SHIPPED


@pytest.mark.django_db
def test_buy_again_re_adds_items_to_cart():
    user = UserFactory(email="buyagain@example.com")
    client, order_number, product = _place_order(user, product_qty=2)

    # cart was cleared by checkout; buy-again should refill it
    response = client.post(reverse("order-buy-again", kwargs={"order_number": order_number}))
    assert response.status_code == 200
    assert product.name in response.data["added"]

    cart_response = client.get(reverse("cart-detail"))
    assert cart_response.data["items"][0]["qty"] == 2


@pytest.mark.django_db
def test_buy_again_skips_inactive_products():
    user = UserFactory(email="buyagaininactive@example.com")
    client, order_number, product = _place_order(user)

    product.is_active = False
    product.save(update_fields=["is_active"])

    response = client.post(reverse("order-buy-again", kwargs={"order_number": order_number}))
    assert product.name in response.data["skipped"]
    assert response.data["added"] == []


class TestOrderStatusMachine:
    @pytest.mark.django_db
    def test_forward_transitions_allowed(self):
        user = UserFactory(email="machine@example.com")
        _, order_number, _ = _place_order(user)
        order = Order.objects.get(order_number=order_number)

        order.transition_to(Order.Status.SHIPPED)
        order.transition_to(Order.Status.OUT_FOR_DELIVERY)
        order.transition_to(Order.Status.DELIVERED)
        assert order.status == Order.Status.DELIVERED

    @pytest.mark.django_db
    def test_cannot_skip_a_step(self):
        user = UserFactory(email="skipstep@example.com")
        _, order_number, _ = _place_order(user)
        order = Order.objects.get(order_number=order_number)

        with pytest.raises(ValueError):
            order.transition_to(Order.Status.DELIVERED)

    @pytest.mark.django_db
    def test_cannot_transition_out_of_terminal_state(self):
        user = UserFactory(email="terminal@example.com")
        _, order_number, _ = _place_order(user)
        order = Order.objects.get(order_number=order_number)
        order.transition_to(Order.Status.CANCELLED)

        with pytest.raises(ValueError):
            order.transition_to(Order.Status.SHIPPED)

    @pytest.mark.django_db
    def test_can_cancel_from_shipped(self):
        user = UserFactory(email="shipcancel@example.com")
        _, order_number, _ = _place_order(user)
        order = Order.objects.get(order_number=order_number)
        order.transition_to(Order.Status.SHIPPED)
        order.transition_to(Order.Status.CANCELLED)
        assert order.status == Order.Status.CANCELLED
