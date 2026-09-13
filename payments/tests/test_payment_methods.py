import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from payments.models import PaymentMethodToken

from .factories import PaymentMethodTokenFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_add_payment_method_never_receives_raw_card_data():
    user = UserFactory(email="payer@example.com")
    client = authed_client(user)

    response = client.post(
        reverse("payment-method-list"),
        {"gateway": "paystack", "token": "tok_abc123", "brand": "visa", "last4": "4242", "expiry_month": 12, "expiry_year": 2030},
    )
    assert response.status_code == 201
    assert "token" not in response.data  # opaque token isn't echoed back
    assert response.data["last4"] == "4242"
    assert response.data["is_default"] is True


@pytest.mark.django_db
def test_second_method_not_default_unless_set():
    user = UserFactory(email="payer2@example.com")
    PaymentMethodTokenFactory(user=user, is_default=True)
    client = authed_client(user)

    response = client.post(
        reverse("payment-method-list"), {"gateway": "flutterwave", "token": "tok_xyz", "brand": "mtn_momo"}
    )
    assert response.data["is_default"] is False


@pytest.mark.django_db
def test_set_default_payment_method():
    user = UserFactory(email="payer3@example.com")
    first = PaymentMethodTokenFactory(user=user, is_default=True)
    second = PaymentMethodTokenFactory(user=user, is_default=False)

    client = authed_client(user)
    response = client.post(reverse("payment-method-set-default", kwargs={"pk": second.id}))
    assert response.status_code == 200

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_default is False
    assert second.is_default is True


@pytest.mark.django_db
def test_deleting_default_method_promotes_another():
    user = UserFactory(email="payer4@example.com")
    default_method = PaymentMethodTokenFactory(user=user, is_default=True)
    other = PaymentMethodTokenFactory(user=user, is_default=False)

    client = authed_client(user)
    response = client.delete(reverse("payment-method-detail", kwargs={"pk": default_method.id}))
    assert response.status_code == 204

    other.refresh_from_db()
    assert other.is_default is True


@pytest.mark.django_db
def test_deleting_only_method_is_blocked():
    user = UserFactory(email="payer5@example.com")
    only = PaymentMethodTokenFactory(user=user, is_default=True)

    client = authed_client(user)
    response = client.delete(reverse("payment-method-detail", kwargs={"pk": only.id}))
    assert response.status_code == 400
    assert PaymentMethodToken.objects.filter(id=only.id).exists()


@pytest.mark.django_db
def test_methods_scoped_to_owner():
    owner = UserFactory(email="owner3@example.com")
    other = UserFactory(email="other3@example.com")
    PaymentMethodTokenFactory(user=owner)
    PaymentMethodTokenFactory(user=other)

    client = authed_client(owner)
    response = client.get(reverse("payment-method-list"))
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    assert len(results) == 1


@pytest.mark.django_db
def test_payment_methods_require_authentication():
    client = APIClient()
    response = client.get(reverse("payment-method-list"))
    assert response.status_code == 401
