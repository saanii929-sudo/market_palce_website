import pytest
from django.urls import reverse

from accounts.models import User
from accounts.tests.factories import UserFactory
from orders.tests.factories import OrderFactory

from .test_catalog_management import admin_client


@pytest.mark.django_db
def test_admin_can_delete_a_user_with_no_orders():
    client, _ = admin_client()
    user = UserFactory(email="delete-me@example.com")

    response = client.post(reverse("web-console-user-delete", args=[user.id]))
    assert response.status_code == 302
    assert not User.objects.filter(id=user.id).exists()


@pytest.mark.django_db
def test_deleting_a_user_with_orders_deactivates_instead():
    client, _ = admin_client()
    user = UserFactory(email="has-orders@example.com", is_active=True)
    OrderFactory(user=user)

    response = client.post(reverse("web-console-user-delete", args=[user.id]))
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.is_active is False


@pytest.mark.django_db
def test_cannot_delete_a_superuser():
    client, _ = admin_client()
    superuser = UserFactory(email="other-admin@example.com", is_superuser=True, is_staff=True)

    response = client.post(reverse("web-console-user-delete", args=[superuser.id]))
    assert response.status_code == 302
    assert User.objects.filter(id=superuser.id).exists()


@pytest.mark.django_db
def test_cannot_delete_own_account():
    client, admin = admin_client()

    response = client.post(reverse("web-console-user-delete", args=[admin.id]))
    assert response.status_code == 302
    assert User.objects.filter(id=admin.id).exists()


@pytest.mark.django_db
def test_non_admin_cannot_delete_users():
    from django.test import Client

    other_user = UserFactory()
    target = UserFactory()
    client = Client()
    client.force_login(other_user)

    response = client.post(reverse("web-console-user-delete", args=[target.id]))
    assert response.status_code == 302
    assert User.objects.filter(id=target.id).exists()
