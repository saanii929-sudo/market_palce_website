from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from accounts.services.social import SocialAuthError

from .factories import UserFactory


def _firebase_payload(email="shopper@example.com", name="Ama Shopper", uid="firebase-uid-1", provider="google.com"):
    payload = {"email": email, "name": name, "uid": uid}
    if provider is not None:
        payload["firebase"] = {"sign_in_provider": provider}
    return payload


@pytest.mark.django_db
@patch("accounts.services.social._get_firebase_app", return_value="dummy-app")
@patch("firebase_admin.auth.verify_id_token")
def test_firebase_login_creates_new_verified_user(mock_verify, mock_app):
    mock_verify.return_value = _firebase_payload()

    response = APIClient().post(reverse("social-firebase"), {"token": "fake-firebase-token"})

    assert response.status_code == 201
    assert response.data["access"] and response.data["refresh"]
    user = User.objects.get(email="shopper@example.com")
    assert user.full_name == "Ama Shopper"
    assert user.is_email_verified is True
    assert not user.has_usable_password()


@pytest.mark.django_db
@patch("accounts.services.social._get_firebase_app", return_value="dummy-app")
@patch("firebase_admin.auth.verify_id_token")
def test_firebase_login_logs_in_existing_user(mock_verify, mock_app):
    existing = UserFactory(email="returning@example.com", is_email_verified=False)
    mock_verify.return_value = _firebase_payload(email="returning@example.com", uid="firebase-uid-2")

    response = APIClient().post(reverse("social-firebase"), {"token": "fake-firebase-token"})

    assert response.status_code == 200
    existing.refresh_from_db()
    assert existing.is_email_verified is True
    assert User.objects.filter(email="returning@example.com").count() == 1


@pytest.mark.django_db
@patch("accounts.services.social._get_firebase_app", return_value="dummy-app")
@patch("firebase_admin.auth.verify_id_token")
def test_firebase_login_rejects_non_google_provider(mock_verify, mock_app):
    mock_verify.return_value = _firebase_payload(provider="password")

    response = APIClient().post(reverse("social-firebase"), {"token": "fake-firebase-token"})

    assert response.status_code == 400
    assert "Google" in response.data["detail"]


@pytest.mark.django_db
@patch("accounts.services.social._get_firebase_app", return_value="dummy-app")
@patch("firebase_admin.auth.verify_id_token")
def test_firebase_login_rejects_invalid_token(mock_verify, mock_app):
    from firebase_admin.exceptions import InvalidArgumentError

    mock_verify.side_effect = InvalidArgumentError("bad token")

    response = APIClient().post(reverse("social-firebase"), {"token": "garbage"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_firebase_login_fails_cleanly_when_not_configured(settings):
    settings.FIREBASE_CREDENTIALS_PATH = ""
    with patch("accounts.services.social._firebase_app", None):
        response = APIClient().post(reverse("social-firebase"), {"token": "whatever"})
    assert response.status_code == 400
    assert "not configured" in response.data["detail"]


@pytest.mark.django_db
@patch("accounts.services.social._get_firebase_app", return_value="dummy-app")
@patch("firebase_admin.auth.verify_id_token")
def test_firebase_login_requires_email(mock_verify, mock_app):
    mock_verify.return_value = _firebase_payload(email=None)

    response = APIClient().post(reverse("social-firebase"), {"token": "fake-firebase-token"})
    assert response.status_code == 400
