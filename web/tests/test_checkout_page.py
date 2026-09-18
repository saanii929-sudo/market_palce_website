import pytest
from django.test import Client
from django.urls import reverse

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.tests.factories import ProductFactory
from orders.tests.factories import DeliveryMethodFactory, PaymentMethodFactory


@pytest.mark.django_db
def test_checkout_page_renders_with_stepper_and_hidden_payment_step():
    user = UserFactory()
    AddressFactory(user=user)
    DeliveryMethodFactory()
    PaymentMethodFactory(code="card", name="Card")
    product = ProductFactory(price="10.00", stock_qty=5)

    client = Client()
    client.force_login(user)
    client.post(reverse("web-cart-add"), {"product_id": product.id, "qty": 1})

    response = client.get(reverse("web-checkout"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'data-checkout-step="2"' in content
    assert 'data-checkout-step="3" hidden' in content
    assert "checkout-submit-btn" in content


@pytest.mark.django_db
def test_order_confirmation_shows_review_step_when_just_placed():
    from orders.services.order_placement import place_order

    user = UserFactory()
    address = AddressFactory(user=user)
    delivery = DeliveryMethodFactory()
    delivery.refresh_from_db()  # DeliveryMethodFactory(price="5.00") leaves the in-memory
    # attribute a str until reloaded; a real request path always fetches fresh from the DB.
    payment_method = PaymentMethodFactory(code="cash_on_delivery", name="Cash on Delivery")
    product = ProductFactory(price="10.00", stock_qty=5)

    client = Client()
    client.force_login(user)
    client.post(reverse("web-cart-add"), {"product_id": product.id, "qty": 1})

    from cart.models import Cart

    cart = Cart.objects.get(user=user)
    order = place_order(user=user, cart=cart, address=address, delivery_method=delivery, payment_method=payment_method)

    response = client.get(reverse("web-order-confirmation", kwargs={"order_number": order.order_number}))
    assert response.status_code == 200
    content = response.content.decode()
    assert "Review" in content
    assert "Order placed!" in content
