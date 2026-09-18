from unittest.mock import patch

import pytest
from django.test import Client
from django.urls import reverse

from accounts.models import User
from accounts.services.social import SocialAuthError
from accounts.tests.factories import UserFactory


def _post_with_g_csrf(client, token="g-csrf-token-1", **fields):
    client.cookies["g_csrf_token"] = token
    return client.post(reverse("web-google-callback"), {"g_csrf_token": token, **fields})


@pytest.mark.django_db
def test_login_page_shows_real_google_button_when_configured(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = "test-client-id.apps.googleusercontent.com"
    response = Client().get(reverse("web-login"))
    content = response.content.decode()
    assert "accounts.google.com/gsi/client" in content
    assert 'id="google-signin-btn"' in content
    assert "ux_mode: 'redirect'" in content
    assert "test-client-id.apps.googleusercontent.com" in content


@pytest.mark.django_db
def test_login_page_shows_disabled_button_when_not_configured(settings):
    settings.GOOGLE_OAUTH_CLIENT_ID = ""
    response = Client().get(reverse("web-login"))
    content = response.content.decode()
    assert "accounts.google.com/gsi/client" not in content
    assert 'id="google-signin-btn"' not in content
    assert "disabled" in content


@pytest.mark.django_db
@patch("accounts.services.social.verify_google_token")
def test_google_callback_creates_and_logs_in_new_user(mock_verify):
    mock_verify.return_value = {
        "email": "newshopper@example.com", "full_name": "New Shopper", "provider_id": "google-sub-1",
    }

    client = Client()
    response = _post_with_g_csrf(client, credential="fake-id-token")

    assert response.status_code == 302
    assert response.url == reverse("web-home")
    user = User.objects.get(email="newshopper@example.com")
    assert user.full_name == "New Shopper"
    assert user.is_email_verified is True
    assert not user.has_usable_password()

    home = client.get(reverse("web-home"))
    assert home.context["user"].is_authenticated


@pytest.mark.django_db
@patch("accounts.services.social.verify_google_token")
def test_google_callback_logs_in_existing_user(mock_verify):
    existing = UserFactory(email="returning@example.com", is_email_verified=False)
    mock_verify.return_value = {"email": "returning@example.com", "full_name": "", "provider_id": "google-sub-2"}

    response = _post_with_g_csrf(Client(), credential="fake-id-token")

    assert response.status_code == 302
    existing.refresh_from_db()
    assert existing.is_email_verified is True
    assert User.objects.filter(email="returning@example.com").count() == 1


@pytest.mark.django_db
@patch("accounts.services.social.verify_google_token")
def test_google_callback_redirects_to_login_on_invalid_token(mock_verify):
    mock_verify.side_effect = SocialAuthError("Invalid Google token")

    response = _post_with_g_csrf(Client(), credential="garbage")

    assert response.status_code == 302
    assert response.url == reverse("web-login")
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_google_callback_rejects_mismatched_csrf_cookie():
    client = Client()
    client.cookies["g_csrf_token"] = "cookie-value"
    response = client.post(reverse("web-google-callback"), {"g_csrf_token": "different-body-value", "credential": "x"})

    assert response.status_code == 302
    assert response.url == reverse("web-login")
    assert User.objects.count() == 0


@pytest.mark.django_db
def test_google_callback_rejects_missing_csrf_cookie():
    response = Client().post(reverse("web-google-callback"), {"credential": "x"})

    assert response.status_code == 302
    assert response.url == reverse("web-login")
    assert User.objects.count() == 0
