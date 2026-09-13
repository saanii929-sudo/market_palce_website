import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from ..models import StaticPage


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_get_static_page_by_slug(api_client):
    StaticPage.objects.create(slug=StaticPage.Slug.ABOUT, title="About Us", body="<p>We sell sportswear.</p>")

    response = api_client.get(reverse("static-page-detail", kwargs={"slug": "about"}))
    assert response.status_code == 200
    assert response.data["title"] == "About Us"


@pytest.mark.django_db
def test_get_missing_static_page_404(api_client):
    response = api_client.get(reverse("static-page-detail", kwargs={"slug": "terms"}))
    assert response.status_code == 404


@pytest.mark.django_db
def test_static_pages_are_public(api_client):
    StaticPage.objects.create(slug=StaticPage.Slug.PRIVACY, title="Privacy Policy", body="...")
    response = api_client.get(reverse("static-page-detail", kwargs={"slug": "privacy"}))
    assert response.status_code == 200
