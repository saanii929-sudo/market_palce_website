import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import ProductFactory, ProductVariantFactory

from ..models import Cart, CartItem
from .factories import CouponFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_guest_can_add_item_to_cart(api_client):
    product = ProductFactory(price="20.00")

    response = api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 2})
    assert response.status_code == 200
    assert response.data["subtotal"] == "40.00"
    assert len(response.data["items"]) == 1


@pytest.mark.django_db
def test_adding_same_product_twice_increments_qty(api_client):
    product = ProductFactory(price="10.00")

    api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    response = api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 2})

    assert len(response.data["items"]) == 1
    assert response.data["items"][0]["qty"] == 3


@pytest.mark.django_db
def test_add_item_with_variant(api_client):
    product = ProductFactory(price="30.00")
    variant = ProductVariantFactory(product=product, size="L")

    response = api_client.post(reverse("cart-item-add"), {"product_id": product.id, "variant_id": variant.id, "qty": 1})
    assert response.status_code == 200
    assert response.data["items"][0]["variant"] == variant.id


@pytest.mark.django_db
def test_update_and_remove_cart_item(api_client):
    product = ProductFactory(price="15.00")
    add_response = api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})
    item_id = add_response.data["items"][0]["id"]

    update_response = api_client.patch(reverse("cart-item-detail", kwargs={"item_id": item_id}), {"qty": 5})
    assert update_response.data["items"][0]["qty"] == 5
    assert update_response.data["subtotal"] == "75.00"

    delete_response = api_client.delete(reverse("cart-item-detail", kwargs={"item_id": item_id}))
    assert delete_response.data["items"] == []


@pytest.mark.django_db
def test_authenticated_user_cart_is_separate_from_guest(api_client):
    product = ProductFactory(price="10.00")
    user = UserFactory(email="cartuser@example.com")

    api_client.force_authenticate(user=user)
    api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    assert Cart.objects.filter(user=user).exists()
    assert CartItem.objects.filter(cart__user=user).count() == 1


@pytest.mark.django_db
def test_apply_and_remove_coupon(api_client):
    product = ProductFactory(price="100.00")
    coupon = CouponFactory(discount_type="percentage", value="10.00")
    api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    apply_response = api_client.post(reverse("cart-coupon"), {"code": coupon.code})
    assert apply_response.status_code == 200
    assert apply_response.data["discount_amount"] == "10.00"
    assert apply_response.data["total"] == "90.00"
    assert apply_response.data["coupon_code"] == coupon.code

    remove_response = api_client.delete(reverse("cart-coupon"))
    assert remove_response.data["coupon_code"] is None
    assert remove_response.data["discount_amount"] == "0.00"


@pytest.mark.django_db
def test_coupon_below_minimum_order_amount_rejected(api_client):
    product = ProductFactory(price="10.00")
    coupon = CouponFactory(discount_type="fixed", value="5.00", min_order_amount="50.00")
    api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    response = api_client.post(reverse("cart-coupon"), {"code": coupon.code})
    assert response.status_code == 400


@pytest.mark.django_db
def test_cart_clear_empties_items(api_client):
    product = ProductFactory(price="10.00")
    api_client.post(reverse("cart-item-add"), {"product_id": product.id, "qty": 1})

    response = api_client.post(reverse("cart-clear"))
    assert response.data["items"] == []
