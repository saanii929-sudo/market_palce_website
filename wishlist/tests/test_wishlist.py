import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import ProductFactory

from ..models import WishlistItem


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_guest_can_toggle_wishlist_item(api_client):
    product = ProductFactory()

    response = api_client.post(reverse("wishlist-toggle"), {"product_id": product.id})
    assert response.status_code == 200
    assert response.data["wishlisted"] is True
    assert WishlistItem.objects.filter(product=product, session_key__isnull=False).exists()


@pytest.mark.django_db
def test_toggle_twice_removes_item(api_client):
    product = ProductFactory()

    first = api_client.post(reverse("wishlist-toggle"), {"product_id": product.id})
    second = api_client.post(reverse("wishlist-toggle"), {"product_id": product.id})

    assert first.data["wishlisted"] is True
    assert second.data["wishlisted"] is False
    assert not WishlistItem.objects.filter(product=product).exists()


@pytest.mark.django_db
def test_authenticated_user_wishlist_is_separate_from_guest(api_client):
    product = ProductFactory()
    user = UserFactory(email="wisher@example.com")

    api_client.force_authenticate(user=user)
    api_client.post(reverse("wishlist-toggle"), {"product_id": product.id})

    assert WishlistItem.objects.filter(user=user, product=product).exists()
    assert not WishlistItem.objects.filter(session_key__isnull=False).exists()


@pytest.mark.django_db
def test_wishlist_list_scoped_to_owner(api_client):
    owner = UserFactory(email="owner@example.com")
    other = UserFactory(email="other@example.com")
    product_a = ProductFactory(name="A")
    product_b = ProductFactory(name="B")

    WishlistItem.objects.create(user=owner, product=product_a)
    WishlistItem.objects.create(user=other, product=product_b)

    client = APIClient()
    client.force_authenticate(user=owner)
    response = client.get(reverse("wishlist-list"))

    results = response.data["results"] if isinstance(response.data, dict) else response.data
    assert len(results) == 1
    assert results[0]["product"]["name"] == "A"


@pytest.mark.django_db
def test_toggle_rejects_inactive_product(api_client):
    product = ProductFactory(is_active=False)
    response = api_client.post(reverse("wishlist-toggle"), {"product_id": product.id})
    assert response.status_code == 400


@pytest.mark.django_db
def test_guest_wishlist_merges_into_user_wishlist_on_login():
    product = ProductFactory()
    user = UserFactory(email="merger@example.com", is_email_verified=True)

    guest_client = APIClient()
    guest_client.post(reverse("wishlist-toggle"), {"product_id": product.id})

    login_response = guest_client.post(
        reverse("login"), {"identifier": "merger@example.com", "password": "StrongPass123!"}
    )
    assert login_response.status_code == 200

    assert WishlistItem.objects.filter(user=user, product=product).exists()
    assert not WishlistItem.objects.filter(session_key__isnull=False).exists()
