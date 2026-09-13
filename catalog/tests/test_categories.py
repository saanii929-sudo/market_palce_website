import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import CategoryFactory, SubcategoryFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_category_list_is_public_and_excludes_inactive(api_client):
    CategoryFactory(name="Football")
    CategoryFactory(name="Rugby", is_active=False)

    response = api_client.get(reverse("catalog-category-list"))
    assert response.status_code == 200
    names = [c["name"] for c in response.data]
    assert "Football" in names
    assert "Rugby" not in names


@pytest.mark.django_db
def test_category_list_nests_subcategories(api_client):
    football = CategoryFactory(name="Football")
    SubcategoryFactory(category=football, name="Boots")
    SubcategoryFactory(category=football, name="Jerseys")

    response = api_client.get(reverse("catalog-category-list"))
    football_data = next(c for c in response.data if c["name"] == "Football")
    subcategory_names = {s["name"] for s in football_data["subcategories"]}
    assert subcategory_names == {"Boots", "Jerseys"}
