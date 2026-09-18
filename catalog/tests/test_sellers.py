import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import ProductFactory, SellerFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_seller_detail_is_public_and_returns_profile(api_client):
    seller = SellerFactory(business_name="Sport Mart", slug="sport-mart", tagline="Gear up.")

    response = api_client.get(reverse("seller-detail", kwargs={"slug": "sport-mart"}))
    assert response.status_code == 200
    assert response.data["business_name"] == "Sport Mart"
    assert response.data["tagline"] == "Gear up."


@pytest.mark.django_db
def test_seller_detail_counts_only_active_products(api_client):
    seller = SellerFactory(slug="active-count")
    ProductFactory(seller=seller, is_active=True)
    ProductFactory(seller=seller, is_active=True)
    ProductFactory(seller=seller, is_active=False)

    response = api_client.get(reverse("seller-detail", kwargs={"slug": "active-count"}))
    assert response.data["product_count"] == 2


@pytest.mark.django_db
def test_seller_detail_404_for_unknown_slug(api_client):
    response = api_client.get(reverse("seller-detail", kwargs={"slug": "does-not-exist"}))
    assert response.status_code == 404


@pytest.mark.django_db
def test_seller_detail_embeds_only_active_products(api_client):
    seller = SellerFactory(slug="with-products")
    ProductFactory(seller=seller, name="Active One", is_active=True)
    ProductFactory(seller=seller, name="Active Two", is_active=True)
    ProductFactory(seller=seller, name="Hidden", is_active=False)
    other_seller_product = ProductFactory(name="Someone Else's", is_active=True)

    response = api_client.get(reverse("seller-detail", kwargs={"slug": "with-products"}))
    assert response.status_code == 200
    names = {p["name"] for p in response.data["products"]}
    assert names == {"Active One", "Active Two"}
    assert other_seller_product.name not in names


@pytest.mark.django_db
def test_seller_detail_products_ordered_by_sold_count_and_capped():
    from catalog.serializers import SELLER_DETAIL_PRODUCT_LIMIT

    seller = SellerFactory(slug="bestsellers")
    for i in range(SELLER_DETAIL_PRODUCT_LIMIT + 3):
        ProductFactory(seller=seller, sold_count=i)

    response = APIClient().get(reverse("seller-detail", kwargs={"slug": "bestsellers"}))
    assert len(response.data["products"]) == SELLER_DETAIL_PRODUCT_LIMIT
    sold_counts = [p["sold_count"] for p in response.data["products"]]
    assert sold_counts == sorted(sold_counts, reverse=True)
