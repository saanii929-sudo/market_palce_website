import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import BrandFactory, ProductFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_brand_detail_is_public_and_returns_profile(api_client):
    brand = BrandFactory(name="Baseline", slug="baseline")

    response = api_client.get(reverse("brand-detail", kwargs={"slug": "baseline"}))
    assert response.status_code == 200
    assert response.data["name"] == "Baseline"
    assert response.data["slug"] == "baseline"


@pytest.mark.django_db
def test_brand_detail_counts_only_active_products():
    brand = BrandFactory(slug="active-count")
    ProductFactory(brand=brand, is_active=True)
    ProductFactory(brand=brand, is_active=True)
    ProductFactory(brand=brand, is_active=False)

    response = APIClient().get(reverse("brand-detail", kwargs={"slug": "active-count"}))
    assert response.data["product_count"] == 2


@pytest.mark.django_db
def test_brand_detail_embeds_only_its_own_active_products():
    brand = BrandFactory(slug="with-products")
    ProductFactory(brand=brand, name="Active One", is_active=True)
    ProductFactory(brand=brand, name="Hidden", is_active=False)
    other_brand_product = ProductFactory(name="Someone Else's", is_active=True)

    response = APIClient().get(reverse("brand-detail", kwargs={"slug": "with-products"}))
    assert response.status_code == 200
    names = {p["name"] for p in response.data["products"]}
    assert names == {"Active One"}
    assert other_brand_product.name not in names


@pytest.mark.django_db
def test_brand_detail_404s_for_unknown_or_inactive_brand():
    inactive = BrandFactory(slug="inactive-brand", is_active=False)

    assert APIClient().get(reverse("brand-detail", kwargs={"slug": "does-not-exist"})).status_code == 404
    assert APIClient().get(reverse("brand-detail", kwargs={"slug": inactive.slug})).status_code == 404


@pytest.mark.django_db
def test_product_list_filters_by_brand_query_param(api_client):
    brand = BrandFactory(slug="filter-brand")
    ProductFactory(brand=brand, name="In brand")
    ProductFactory(name="Other brand")

    response = api_client.get(reverse("product-list"), {"brand": "filter-brand"})
    names = {p["name"] for p in response.data["results"]}
    assert names == {"In brand"}
