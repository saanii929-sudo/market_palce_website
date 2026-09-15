import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from discovery.models import RecentlyViewed
from reviews.tests.factories import ReviewFactory

from .factories import BrandFactory, CategoryFactory, ProductFactory, SellerFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_product_list_is_public(api_client):
    ProductFactory(name="Football Boots")
    ProductFactory(name="Inactive Product", is_active=False)

    response = api_client.get(reverse("product-list"))
    assert response.status_code == 200
    names = [p["name"] for p in response.data["results"]]
    assert "Football Boots" in names
    assert "Inactive Product" not in names


@pytest.mark.django_db
def test_product_list_filters_by_category_brand_seller(api_client):
    category = CategoryFactory(slug="football")
    other_category = CategoryFactory(slug="basketball")
    brand = BrandFactory(slug="nike")
    seller = SellerFactory(slug="sportmart")

    match = ProductFactory(name="Match", category=category, brand=brand, seller=seller)
    ProductFactory(name="NoMatch", category=other_category)

    response = api_client.get(reverse("product-list"), {"category": "football"})
    assert [p["name"] for p in response.data["results"]] == ["Match"]

    response = api_client.get(reverse("product-list"), {"brand": "nike"})
    assert [p["name"] for p in response.data["results"]] == ["Match"]

    response = api_client.get(reverse("product-list"), {"seller": "sportmart"})
    assert [p["name"] for p in response.data["results"]] == ["Match"]


@pytest.mark.django_db
def test_category_product_list_endpoint_scopes_to_category(api_client):
    category = CategoryFactory(slug="football")
    other_category = CategoryFactory(slug="basketball")
    brand = BrandFactory(slug="nike")

    match = ProductFactory(name="Match", category=category, brand=brand)
    ProductFactory(name="NoMatch", category=other_category)

    response = api_client.get(reverse("category-product-list", kwargs={"slug": "football"}))
    assert response.status_code == 200
    assert [p["name"] for p in response.data["results"]] == ["Match"]

    # Brand/sort filters still apply within the category scope.
    response = api_client.get(reverse("category-product-list", kwargs={"slug": "football"}), {"brand": "nike"})
    assert [p["name"] for p in response.data["results"]] == ["Match"]

    response = api_client.get(reverse("category-product-list", kwargs={"slug": "football"}), {"brand": "adidas"})
    assert response.data["results"] == []


@pytest.mark.django_db
def test_category_product_list_404s_for_unknown_or_inactive_category(api_client):
    CategoryFactory(slug="inactive-category", is_active=False)

    response = api_client.get(reverse("category-product-list", kwargs={"slug": "does-not-exist"}))
    assert response.status_code == 404

    response = api_client.get(reverse("category-product-list", kwargs={"slug": "inactive-category"}))
    assert response.status_code == 404


@pytest.mark.django_db
def test_product_list_sort_price_asc(api_client):
    ProductFactory(name="Expensive", price="100.00")
    ProductFactory(name="Cheap", price="10.00")

    response = api_client.get(reverse("product-list"), {"sort": "price_asc"})
    names = [p["name"] for p in response.data["results"]]
    assert names == ["Cheap", "Expensive"]


@pytest.mark.django_db
def test_product_detail_includes_related_products():
    category = CategoryFactory()
    product = ProductFactory(category=category, name="Main")
    related = ProductFactory(category=category, name="Related")
    ProductFactory(name="Other category")

    client = APIClient()
    response = client.get(reverse("product-detail", kwargs={"slug": product.slug}))

    assert response.status_code == 200
    related_names = [p["name"] for p in response.data["related_products"]]
    assert "Related" in related_names
    assert "Main" not in related_names
    assert "Other category" not in related_names


@pytest.mark.django_db
def test_product_detail_shows_discount_percent():
    product = ProductFactory(price="80.00", original_price="100.00")

    client = APIClient()
    response = client.get(reverse("product-detail", kwargs={"slug": product.slug}))
    assert response.data["discount_percent"] == 20


@pytest.mark.django_db
def test_product_view_tracking_increments_count_and_upserts_recently_viewed():
    product = ProductFactory(view_count=0)
    user = UserFactory(email="viewer@example.com")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(reverse("product-track-view", kwargs={"slug": product.slug}))
    assert response.status_code == 204

    product.refresh_from_db()
    assert product.view_count == 1
    assert RecentlyViewed.objects.filter(user=user, product=product).exists()

    client.post(reverse("product-track-view", kwargs={"slug": product.slug}))
    product.refresh_from_db()
    assert product.view_count == 2
    assert RecentlyViewed.objects.filter(user=user, product=product).count() == 1


@pytest.mark.django_db
def test_reviews_list_includes_histogram_and_is_paginated():
    product = ProductFactory()
    ReviewFactory(product=product, rating=5)
    ReviewFactory(product=product, rating=3)

    product.refresh_from_db()
    assert product.review_count == 2
    assert float(product.avg_rating) == 4.0

    client = APIClient()
    response = client.get(reverse("product-reviews", kwargs={"slug": product.slug}))
    assert response.status_code == 200
    assert response.data["histogram"]["5"] == 1
    assert response.data["histogram"]["3"] == 1
    assert response.data["count"] == 2
    assert len(response.data["results"]) == 2
    assert response.data["avg_rating"] == product.avg_rating
    assert response.data["review_count"] == 2
