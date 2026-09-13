import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import BrandFactory, ProductFactory

from ..models import SearchQuery


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_search_suggest_matches_products_and_brands(api_client):
    ProductFactory(name="Nike Football Boots")
    ProductFactory(name="Basketball")
    BrandFactory(name="Nike")

    response = api_client.get(reverse("search-suggest"), {"q": "nike"})
    assert response.status_code == 200
    product_names = [p["name"] for p in response.data["products"]]
    brand_names = [b["name"] for b in response.data["brands"]]
    assert "Nike Football Boots" in product_names
    assert "Nike" in brand_names


@pytest.mark.django_db
def test_search_suggest_empty_query_returns_empty_lists(api_client):
    response = api_client.get(reverse("search-suggest"))
    assert response.data == {"products": [], "brands": [], "categories": [], "sellers": []}


@pytest.mark.django_db
def test_search_log_and_recent(api_client):
    user = UserFactory(email="searcher@example.com")
    client = APIClient()
    client.force_authenticate(user=user)

    client.post(reverse("search-log"), {"query_text": "football boots"})
    client.post(reverse("search-log"), {"query_text": "jerseys"})
    client.post(reverse("search-log"), {"query_text": "football boots"})

    response = client.get(reverse("search-recent"))
    assert response.status_code == 200
    query_texts = [q["query_text"] for q in response.data]
    # Most recent distinct query first; the repeated "football boots" search
    # bumps it back to the front and collapses to a single entry.
    assert query_texts == ["football boots", "jerseys"]


@pytest.mark.django_db
def test_search_recent_requires_authentication(api_client):
    response = api_client.get(reverse("search-recent"))
    assert response.status_code == 401


@pytest.mark.django_db
def test_search_popular_aggregates_across_users(api_client):
    SearchQuery.objects.create(query_text="football")
    SearchQuery.objects.create(query_text="football")
    SearchQuery.objects.create(query_text="tennis")

    response = api_client.get(reverse("search-popular"))
    assert response.data["results"][0] == "football"


@pytest.mark.django_db
def test_search_log_allows_anonymous(api_client):
    response = api_client.post(reverse("search-log"), {"query_text": "anonymous search"})
    assert response.status_code == 201
    assert SearchQuery.objects.filter(query_text="anonymous search", user__isnull=True).exists()
