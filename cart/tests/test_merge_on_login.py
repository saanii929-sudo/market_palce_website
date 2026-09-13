import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import ProductFactory

from ..models import Cart


@pytest.mark.django_db
def test_guest_cart_merges_into_user_cart_on_login():
    product_a = ProductFactory(name="A", price="10.00")
    product_b = ProductFactory(name="B", price="20.00")
    user = UserFactory(email="merger@example.com", is_email_verified=True)

    guest_client = APIClient()
    guest_client.post(reverse("cart-item-add"), {"product_id": product_a.id, "qty": 1})
    guest_client.post(reverse("cart-item-add"), {"product_id": product_b.id, "qty": 2})

    login_response = guest_client.post(
        reverse("login"), {"identifier": "merger@example.com", "password": "StrongPass123!"}
    )
    assert login_response.status_code == 200

    user_cart = Cart.objects.get(user=user)
    assert user_cart.items.count() == 2
    assert set(user_cart.items.values_list("product__name", flat=True)) == {"A", "B"}
