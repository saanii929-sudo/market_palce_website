import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import AddressFactory, UserFactory
from cart.tests.factories import CouponFactory
from catalog.tests.factories import ProductFactory, ProductVariantFactory

from ..models import Order, OrderStatusHistory, Payment
from ..services.order_placement import OrderPlacementError, place_order
from .factories import DeliveryMethodFactory, PaymentMethodFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_place_order_happy_path_decrements_stock_and_clears_cart():
    user = UserFactory(email="buyer@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="25.00", stock_qty=10, sold_count=0)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 3})

    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    assert response.status_code == 201
    assert response.data["subtotal"] == "75.00"
    assert response.data["total"] == "80.00"  # +5.00 delivery fee
    assert response.data["status"] == Order.Status.PROCESSING

    product.refresh_from_db()
    assert product.stock_qty == 7
    assert product.sold_count == 3

    order = Order.objects.get(order_number=response.data["order_number"])
    assert order.items.count() == 1
    assert order.items.first().unit_price == 25
    assert OrderStatusHistory.objects.filter(order=order, status=Order.Status.PROCESSING).exists()
    assert Payment.objects.filter(order=order).exists()

    # cart cleared on success
    cart_response = client.get(reverse("cart-detail"))
    assert cart_response.data["items"] == []


@pytest.mark.django_db
def test_place_order_locks_prices_even_if_product_price_changes_later():
    user = UserFactory(email="lockprice@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="50.00", stock_qty=5)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )
    order = Order.objects.get(order_number=response.data["order_number"])

    product.price = "999.00"
    product.save(update_fields=["price"])

    assert order.items.first().unit_price == 50


@pytest.mark.django_db
def test_place_order_fails_with_insufficient_stock_and_rolls_back():
    user = UserFactory(email="oos@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="10.00", stock_qty=1)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 5})

    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    assert response.status_code == 400
    product.refresh_from_db()
    assert product.stock_qty == 1
    assert Order.objects.count() == 0

    # cart is untouched since the transaction rolled back
    cart_response = client.get(reverse("cart-detail"))
    assert len(cart_response.data["items"]) == 1


@pytest.mark.django_db
def test_place_order_with_variant_decrements_variant_stock_not_product_stock():
    user = UserFactory(email="variant@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="40.00", stock_qty=100)
    variant = ProductVariantFactory(product=product, stock_qty=4)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "variant_id": variant.id, "qty": 2})
    client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    variant.refresh_from_db()
    product.refresh_from_db()
    assert variant.stock_qty == 2
    assert product.stock_qty == 100


@pytest.mark.django_db
def test_place_order_empty_cart_raises():
    from cart.models import Cart

    user = UserFactory(email="empty@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    delivery.refresh_from_db()  # DeliveryMethodFactory(price="5.00") leaves the in-memory
    # attribute a str until reloaded; a real request path always fetches fresh from the DB.
    payment_method = PaymentMethodFactory()
    empty_cart = Cart.objects.create(user=user)

    with pytest.raises(OrderPlacementError):
        place_order(user=user, cart=empty_cart, address=address, delivery_method=delivery, payment_method=payment_method)


@pytest.mark.django_db
def test_place_order_applies_valid_coupon_discount():
    user = UserFactory(email="coupon@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="100.00", stock_qty=10)
    coupon = CouponFactory(discount_type="percentage", value="20.00")

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    client.post(reverse("cart-coupon"), {"code": coupon.code})

    response = client.post(
        reverse("order-list"),
        {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id},
    )

    assert response.status_code == 201
    assert response.data["discount_amount"] == "20.00"
    assert response.data["total"] == "85.00"  # 100 - 20 + 5 delivery

    coupon.refresh_from_db()
    assert coupon.times_used == 1


@pytest.mark.django_db
def test_retrying_order_placement_with_same_idempotency_key_returns_same_order():
    user = UserFactory(email="retry@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="20.00", stock_qty=10)

    client = authed_client(user)
    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    payload = {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id}
    first = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="retry-key-1")
    assert first.status_code == 201

    # Simulate a client retry after e.g. a dropped response - the cart is
    # already empty at this point, so a naive re-run would fail differently;
    # the idempotency key should short-circuit before that even matters.
    second = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="retry-key-1")
    assert second.status_code == 200
    assert second.data["order_number"] == first.data["order_number"]

    assert Order.objects.filter(user=user).count() == 1
    product.refresh_from_db()
    assert product.stock_qty == 9  # decremented only once


@pytest.mark.django_db
def test_different_idempotency_keys_place_separate_orders():
    user = UserFactory(email="separate@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="20.00", stock_qty=10)

    client = authed_client(user)
    payload = {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id}

    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    first = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="key-a")

    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    second = client.post(reverse("order-list"), payload, HTTP_IDEMPOTENCY_KEY="key-b")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.data["order_number"] != second.data["order_number"]
    assert Order.objects.filter(user=user).count() == 2


@pytest.mark.django_db
def test_order_placement_without_idempotency_key_is_not_deduplicated():
    user = UserFactory(email="nokeytwice@example.com")
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    payment_method = PaymentMethodFactory()
    product = ProductFactory(price="20.00", stock_qty=10)

    client = authed_client(user)
    payload = {"address_id": address.id, "delivery_method_id": delivery.id, "payment_method_id": payment_method.id}

    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    first = client.post(reverse("order-list"), payload)

    client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    second = client.post(reverse("order-list"), payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert Order.objects.filter(user=user).count() == 2
