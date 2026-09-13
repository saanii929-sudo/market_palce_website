import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import UserInterest
from catalog.tests.factories import CategoryFactory

from .factories import UserFactory


@pytest.fixture
def api_client():
    return APIClient()


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_me_requires_authentication(api_client):
    response = api_client.get(reverse("me"))
    assert response.status_code == 401


@pytest.mark.django_db
def test_me_get_and_patch():
    user = UserFactory(email="me@example.com", full_name="Old Name")
    client = authed_client(user)

    get_response = client.get(reverse("me"))
    assert get_response.status_code == 200
    assert get_response.data["email"] == "me@example.com"

    patch_response = client.patch(reverse("me"), {"full_name": "New Name", "bio": "Loves football"})
    assert patch_response.status_code == 200
    user.refresh_from_db()
    assert user.full_name == "New Name"
    assert user.bio == "Loves football"


@pytest.mark.django_db
def test_me_cannot_change_email_directly():
    user = UserFactory(email="locked@example.com")
    client = authed_client(user)

    client.patch(reverse("me"), {"email": "changed@example.com"})
    user.refresh_from_db()
    assert user.email == "locked@example.com"


@pytest.mark.django_db
def test_set_interests_replaces_existing_set():
    user = UserFactory(email="interests@example.com")
    football = CategoryFactory(name="Football")
    basketball = CategoryFactory(name="Basketball")
    tennis = CategoryFactory(name="Tennis")
    client = authed_client(user)

    first = client.put(reverse("me-interests"), {"category_ids": [football.id, basketball.id]})
    assert first.status_code == 200
    assert set(UserInterest.objects.filter(user=user).values_list("category_id", flat=True)) == {
        football.id,
        basketball.id,
    }

    second = client.put(reverse("me-interests"), {"category_ids": [tennis.id]})
    assert second.status_code == 200
    assert set(UserInterest.objects.filter(user=user).values_list("category_id", flat=True)) == {tennis.id}


@pytest.mark.django_db
def test_set_interests_rejects_unknown_category():
    user = UserFactory(email="badinterest@example.com")
    client = authed_client(user)

    response = client.put(reverse("me-interests"), {"category_ids": [9999]})
    assert response.status_code == 400
