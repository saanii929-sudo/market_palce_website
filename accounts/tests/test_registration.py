import re

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import OTPCode, User

CODE_RE = re.compile(r"\b(\d{6})\b")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_register_with_email_sends_otp_and_creates_unverified_user(api_client, settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

    response = api_client.post(
        reverse("register"),
        {"email": "new@example.com", "password": "StrongPass123!", "full_name": "New User"},
    )

    assert response.status_code == 201
    user = User.objects.get(email="new@example.com")
    assert user.is_email_verified is False
    assert user.check_password("StrongPass123!")

    assert len(mail.outbox) == 1
    assert OTPCode.objects.filter(destination="new@example.com", purpose=OTPCode.Purpose.SIGNUP_VERIFY).exists()


@pytest.mark.django_db
def test_register_requires_email_or_phone(api_client):
    response = api_client.post(reverse("register"), {"password": "StrongPass123!"})
    assert response.status_code == 400


@pytest.mark.django_db
def test_register_with_phone_only(api_client):
    response = api_client.post(
        reverse("register"),
        {"phone": "+233201234567", "password": "StrongPass123!", "full_name": "Phone User"},
    )
    assert response.status_code == 201
    assert User.objects.filter(phone="+233201234567").exists()


@pytest.mark.django_db
def test_full_signup_verification_flow_returns_tokens(api_client, settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

    api_client.post(
        reverse("register"),
        {"email": "flow@example.com", "password": "StrongPass123!", "full_name": "Flow User"},
    )
    code = CODE_RE.search(mail.outbox[0].body).group(1)

    response = api_client.post(
        reverse("otp-verify"),
        {"destination": "flow@example.com", "purpose": OTPCode.Purpose.SIGNUP_VERIFY, "code": code},
    )

    assert response.status_code == 200
    assert "access" in response.data
    assert "refresh" in response.data
    user = User.objects.get(email="flow@example.com")
    assert user.is_email_verified is True
