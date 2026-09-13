import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import UserFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_login_with_email_succeeds(api_client):
    UserFactory(email="login@example.com", is_email_verified=True)

    response = api_client.post(
        reverse("login"), {"identifier": "login@example.com", "password": "StrongPass123!"}
    )

    assert response.status_code == 200
    assert "access" in response.data and "refresh" in response.data


@pytest.mark.django_db
def test_login_with_phone_succeeds(api_client):
    UserFactory(email=None, phone="+233201234567", is_email_verified=False, is_phone_verified=True)

    response = api_client.post(
        reverse("login"), {"identifier": "+233201234567", "password": "StrongPass123!"}
    )

    assert response.status_code == 200


@pytest.mark.django_db
def test_login_wrong_password_fails(api_client):
    UserFactory(email="wrong@example.com", is_email_verified=True)

    response = api_client.post(
        reverse("login"), {"identifier": "wrong@example.com", "password": "nope"}
    )

    assert response.status_code == 401
    assert response.data["code"] == "invalid_credentials"


@pytest.mark.django_db
def test_login_unverified_account_returns_403(api_client):
    UserFactory(email="unverified@example.com", is_email_verified=False)

    response = api_client.post(
        reverse("login"), {"identifier": "unverified@example.com", "password": "StrongPass123!"}
    )

    assert response.status_code == 403
    assert response.data["code"] == "unverified_account"


@pytest.mark.django_db
def test_logout_blacklists_refresh_token(api_client):
    user = UserFactory(email="logout@example.com", is_email_verified=True)
    login_response = api_client.post(
        reverse("login"), {"identifier": "logout@example.com", "password": "StrongPass123!"}
    )
    access = login_response.data["access"]
    refresh = login_response.data["refresh"]

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")
    response = api_client.post(reverse("logout"), {"refresh": refresh})
    assert response.status_code == 204

    refresh_response = api_client.post(reverse("token-refresh"), {"refresh": refresh})
    assert refresh_response.status_code == 401
