import re

import pytest
from django.core import mail
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import OTPCode

from .factories import UserFactory

CODE_RE = re.compile(r"\b(\d{6})\b")


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_forgot_password_sends_code_and_reset_changes_password(api_client, settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    user = UserFactory(email="forgot@example.com", is_email_verified=True)

    forgot_response = api_client.post(reverse("password-forgot"), {"destination": "forgot@example.com"})
    assert forgot_response.status_code == 200
    code = CODE_RE.search(mail.outbox[0].body).group(1)

    reset_response = api_client.post(
        reverse("password-reset"),
        {"destination": "forgot@example.com", "code": code, "new_password": "NewStrongPass456!"},
    )
    assert reset_response.status_code == 200

    user.refresh_from_db()
    assert user.check_password("NewStrongPass456!")


@pytest.mark.django_db
def test_reset_with_wrong_code_fails(api_client):
    UserFactory(email="badcode@example.com", is_email_verified=True)
    api_client.post(reverse("password-forgot"), {"destination": "badcode@example.com"})

    response = api_client.post(
        reverse("password-reset"),
        {"destination": "badcode@example.com", "code": "000000", "new_password": "NewStrongPass456!"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_reset_code_cannot_be_reused(api_client, settings):
    settings.MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}
    UserFactory(email="reuse@example.com", is_email_verified=True)
    api_client.post(reverse("password-forgot"), {"destination": "reuse@example.com"})
    code = CODE_RE.search(mail.outbox[0].body).group(1)

    first = api_client.post(
        reverse("password-reset"),
        {"destination": "reuse@example.com", "code": code, "new_password": "NewStrongPass456!"},
    )
    assert first.status_code == 200

    second = api_client.post(
        reverse("password-reset"),
        {"destination": "reuse@example.com", "code": code, "new_password": "AnotherPass789!"},
    )
    assert second.status_code == 400


@pytest.mark.django_db
def test_forgot_password_unknown_destination_does_not_leak_existence(api_client):
    response = api_client.post(reverse("password-forgot"), {"destination": "ghost@example.com"})
    assert response.status_code == 200
    assert not OTPCode.objects.filter(destination="ghost@example.com").exists()
