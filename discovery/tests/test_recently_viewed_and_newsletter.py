import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import ProductFactory

from ..models import NewsletterSubscriber, RecentlyViewed


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_recently_viewed_requires_authentication(api_client):
    response = api_client.get(reverse("recently-viewed"))
    assert response.status_code == 401


@pytest.mark.django_db
def test_recently_viewed_lists_only_own_products():
    user = UserFactory(email="a@example.com")
    other = UserFactory(email="b@example.com")
    product = ProductFactory(name="Viewed by A")
    other_product = ProductFactory(name="Viewed by B")
    RecentlyViewed.objects.create(user=user, product=product)
    RecentlyViewed.objects.create(user=other, product=other_product)

    client = APIClient()
    client.force_authenticate(user=user)
    response = client.get(reverse("recently-viewed"))

    names = [r["product"]["name"] for r in response.data["results"]]
    assert names == ["Viewed by A"]


@pytest.mark.django_db
def test_newsletter_subscribe_creates_subscriber(api_client):
    response = api_client.post(reverse("newsletter-subscribe"), {"email": "fan@example.com"})
    assert response.status_code == 201
    assert NewsletterSubscriber.objects.filter(email="fan@example.com").exists()


@pytest.mark.django_db
def test_newsletter_subscribe_is_idempotent(api_client):
    api_client.post(reverse("newsletter-subscribe"), {"email": "fan@example.com"})
    response = api_client.post(reverse("newsletter-subscribe"), {"email": "fan@example.com"})
    assert response.status_code == 201
    assert NewsletterSubscriber.objects.filter(email="fan@example.com").count() == 1


@pytest.mark.django_db
def test_newsletter_subscribe_rejects_invalid_email(api_client):
    response = api_client.post(reverse("newsletter-subscribe"), {"email": "not-an-email"})
    assert response.status_code == 400
