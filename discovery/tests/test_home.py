from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import (
    BannerFactory,
    CategoryFactory,
    CollectionFactory,
    FlashDealFactory,
    ProductFactory,
)

from ..models import RecentlyViewed


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_home_includes_featured_and_popular_products(api_client):
    ProductFactory(name="Popular", sold_count=100)
    ProductFactory(name="Unpopular", sold_count=0)
    ProductFactory(name="Featured", is_featured=True)

    response = api_client.get(reverse("home"))
    assert response.status_code == 200

    popular_names = [p["name"] for p in response.data["popular_products"]]
    assert popular_names[0] == "Popular"

    featured_names = [p["name"] for p in response.data["featured_products"]]
    assert "Featured" in featured_names


@pytest.mark.django_db
def test_home_includes_only_live_flash_deals(api_client):
    now = timezone.now()
    live = FlashDealFactory(starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1))
    FlashDealFactory(starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1))

    response = api_client.get(reverse("home"))
    deal_product_ids = [d["product"]["id"] for d in response.data["flash_deals"]]
    assert deal_product_ids == [live.product.id]


@pytest.mark.django_db
def test_home_includes_active_banners_and_collections(api_client):
    BannerFactory(title="Sale Banner", is_active=True)
    BannerFactory(title="Old Banner", is_active=False)
    CollectionFactory(title="Football Gear", is_active=True)

    response = api_client.get(reverse("home"))
    banner_titles = [b["title"] for b in response.data["banners"]]
    assert "Sale Banner" in banner_titles
    assert "Old Banner" not in banner_titles

    collection_titles = [c["title"] for c in response.data["collections"]]
    assert "Football Gear" in collection_titles


@pytest.mark.django_db
def test_home_recommended_falls_back_to_popular_for_anonymous(api_client):
    ProductFactory(name="Popular", sold_count=50)

    response = api_client.get(reverse("home"))
    recommended_names = [p["name"] for p in response.data["recommended_products"]]
    assert "Popular" in recommended_names


@pytest.mark.django_db
def test_home_recommended_uses_user_interests():
    football = CategoryFactory(name="Football")
    other = CategoryFactory(name="Chess")
    football_product = ProductFactory(name="Football Boots", category=football)
    ProductFactory(name="Chess Set", category=other)

    user = UserFactory(email="fan@example.com")
    user.interests.add(football)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(reverse("home"))

    recommended_names = [p["name"] for p in response.data["recommended_products"]]
    assert "Football Boots" in recommended_names
    assert "Chess Set" not in recommended_names


@pytest.mark.django_db
def test_home_recommended_excludes_already_viewed_products():
    football = CategoryFactory(name="Football")
    viewed = ProductFactory(name="Already Viewed", category=football)
    fresh = ProductFactory(name="Fresh Pick", category=football)

    user = UserFactory(email="viewer@example.com")
    user.interests.add(football)
    RecentlyViewed.objects.create(user=user, product=viewed)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(reverse("home"))

    recommended_names = [p["name"] for p in response.data["recommended_products"]]
    assert "Fresh Pick" in recommended_names
    assert "Already Viewed" not in recommended_names
