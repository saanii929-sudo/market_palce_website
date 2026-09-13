from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import OTPCode
from accounts.services.otp import OTPVerificationError, verify_otp

from .factories import OTPCodeFactory, UserFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_verify_wrong_code_fails_and_increments_attempts():
    user = UserFactory(email="otp@example.com", is_email_verified=False)
    otp, raw_code = OTPCode.issue(
        destination="otp@example.com", channel=OTPCode.Channel.EMAIL, purpose=OTPCode.Purpose.SIGNUP_VERIFY, user=user
    )

    with pytest.raises(OTPVerificationError) as exc_info:
        verify_otp("otp@example.com", OTPCode.Purpose.SIGNUP_VERIFY, "000000")

    assert exc_info.value.code == "invalid"
    otp.refresh_from_db()
    assert otp.attempts == 1


@pytest.mark.django_db
def test_otp_locks_after_max_attempts():
    user = UserFactory(email="lock@example.com", is_email_verified=False)
    otp, raw_code = OTPCode.issue(
        destination="lock@example.com", channel=OTPCode.Channel.EMAIL, purpose=OTPCode.Purpose.SIGNUP_VERIFY, user=user
    )

    for _ in range(OTPCode.MAX_ATTEMPTS):
        with pytest.raises(OTPVerificationError):
            verify_otp("lock@example.com", OTPCode.Purpose.SIGNUP_VERIFY, "000000")

    with pytest.raises(OTPVerificationError) as exc_info:
        verify_otp("lock@example.com", OTPCode.Purpose.SIGNUP_VERIFY, raw_code)
    assert exc_info.value.code == "locked"


@pytest.mark.django_db
def test_expired_otp_rejected():
    otp = OTPCodeFactory(expires_at=timezone.now() - timedelta(minutes=1), destination="expired@example.com")

    with pytest.raises(OTPVerificationError) as exc_info:
        verify_otp("expired@example.com", OTPCode.Purpose.SIGNUP_VERIFY, "123456")
    assert exc_info.value.code == "expired"


@pytest.mark.django_db
def test_used_otp_not_returned_as_active():
    otp = OTPCodeFactory(
        destination="used@example.com",
        expires_at=timezone.now() + timedelta(minutes=10),
        is_used=True,
    )

    with pytest.raises(OTPVerificationError) as exc_info:
        verify_otp("used@example.com", OTPCode.Purpose.SIGNUP_VERIFY, "123456")
    assert exc_info.value.code == "not_found"


@pytest.mark.django_db
def test_otp_send_rate_limited_by_destination(api_client):
    payload = {"destination": "burst@example.com", "purpose": OTPCode.Purpose.SIGNUP_VERIFY}

    r1 = api_client.post(reverse("otp-send"), payload)
    r2 = api_client.post(reverse("otp-send"), payload)
    r3 = api_client.post(reverse("otp-send"), payload)

    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r3.status_code == 429


@pytest.mark.django_db
def test_password_reset_otp_send_does_not_reveal_account_existence(api_client):
    response = api_client.post(
        reverse("otp-send"),
        {"destination": "nobody@example.com", "purpose": OTPCode.Purpose.PASSWORD_RESET},
    )
    assert response.status_code == 200
    assert not OTPCode.objects.filter(destination="nobody@example.com").exists()
