import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import AddressFactory, UserFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_create_and_list_addresses():
    user = UserFactory(email="addr@example.com")
    client = authed_client(user)

    response = client.post(
        reverse("address-list"),
        {
            "recipient_name": "Jane Doe",
            "phone": "+233201234567",
            "line1": "123 Independence Ave",
            "city": "Accra",
            "country": "Ghana",
        },
    )
    assert response.status_code == 201

    list_response = client.get(reverse("address-list"))
    results = list_response.data["results"] if isinstance(list_response.data, dict) else list_response.data
    assert len(results) == 1


@pytest.mark.django_db
def test_address_pin_round_trips_through_the_api():
    """Regression test: AddressSerializer used to omit lat/lng entirely, so
    DRF silently dropped them from every request - the address would save
    fine but the pin never persisted. Confirms the fields are now wired
    through create AND back out on read."""
    user = UserFactory(email="pinned@example.com")
    client = authed_client(user)

    response = client.post(
        reverse("address-list"),
        {
            "recipient_name": "Jane Doe", "phone": "+233201234567",
            "line1": "123 Independence Ave", "city": "Accra",
            "lat": "5.603700", "lng": "-0.187000",
        },
    )
    assert response.status_code == 201
    assert response.data["lat"] == "5.603700"
    assert response.data["lng"] == "-0.187000"

    address_id = response.data["id"]
    detail_response = client.get(reverse("address-detail", kwargs={"pk": address_id}))
    assert detail_response.data["lat"] == "5.603700"
    assert detail_response.data["lng"] == "-0.187000"

    from accounts.models import Address

    from decimal import Decimal

    saved = Address.objects.get(id=address_id)
    assert saved.lat == Decimal("5.603700")
    assert saved.lng == Decimal("-0.187000")


@pytest.mark.django_db
def test_address_without_a_pin_still_saves_fine():
    user = UserFactory(email="unpinned@example.com")
    client = authed_client(user)

    response = client.post(
        reverse("address-list"),
        {"recipient_name": "Jane Doe", "phone": "+233201234567", "line1": "123 Ave", "city": "Accra"},
    )
    assert response.status_code == 201
    assert response.data["lat"] is None
    assert response.data["lng"] is None


@pytest.mark.django_db
def test_first_address_is_automatically_default():
    user = UserFactory(email="firstdefault@example.com")
    client = authed_client(user)

    response = client.post(
        reverse("address-list"),
        {"recipient_name": "Jane Doe", "phone": "+233201234567", "line1": "123 Ave", "city": "Accra"},
    )
    assert response.data["is_default"] is True


@pytest.mark.django_db
def test_second_address_is_not_default_unless_set():
    user = UserFactory(email="seconddefault@example.com")
    AddressFactory(user=user, is_default=True)
    client = authed_client(user)

    response = client.post(
        reverse("address-list"),
        {"recipient_name": "Second", "phone": "+233201234567", "line1": "456 Ave", "city": "Kumasi"},
    )
    assert response.data["is_default"] is False


@pytest.mark.django_db
def test_addresses_are_scoped_to_owner():
    owner = UserFactory(email="owner@example.com")
    other = UserFactory(email="other@example.com")
    AddressFactory(user=owner)
    AddressFactory(user=other)

    client = authed_client(owner)
    response = client.get(reverse("address-list"))
    results = response.data["results"] if isinstance(response.data, dict) else response.data
    assert len(results) == 1


@pytest.mark.django_db
def test_setting_default_unsets_previous_default():
    user = UserFactory(email="default@example.com")
    first = AddressFactory(user=user, is_default=True)
    second = AddressFactory(user=user, is_default=False)

    client = authed_client(user)
    response = client.post(reverse("address-set-default", kwargs={"pk": second.id}))
    assert response.status_code == 200

    first.refresh_from_db()
    second.refresh_from_db()
    assert first.is_default is False
    assert second.is_default is True


@pytest.mark.django_db
def test_deleting_default_address_promotes_another():
    user = UserFactory(email="promote@example.com")
    default_addr = AddressFactory(user=user, is_default=True)
    other = AddressFactory(user=user, is_default=False)

    client = authed_client(user)
    response = client.delete(reverse("address-detail", kwargs={"pk": default_addr.id}))
    assert response.status_code == 204

    other.refresh_from_db()
    assert other.is_default is True


@pytest.mark.django_db
def test_deleting_only_address_is_blocked():
    user = UserFactory(email="onlyaddr@example.com")
    only = AddressFactory(user=user, is_default=True)

    client = authed_client(user)
    response = client.delete(reverse("address-detail", kwargs={"pk": only.id}))
    assert response.status_code == 400

    from accounts.models import Address

    assert Address.objects.filter(id=only.id).exists()


@pytest.mark.django_db
def test_addresses_require_authentication():
    client = APIClient()
    response = client.get(reverse("address-list"))
    assert response.status_code == 401
