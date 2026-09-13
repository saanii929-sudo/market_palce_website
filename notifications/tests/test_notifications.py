import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from notifications.models import Notification
from notifications.services import notify

from .factories import NotificationFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_list_notifications_scoped_to_owner():
    owner = UserFactory(email="owner@example.com")
    other = UserFactory(email="other@example.com")
    NotificationFactory(user=owner)
    NotificationFactory(user=other)

    client = authed_client(owner)
    response = client.get(reverse("notification-list"))
    assert response.data["count"] == 1


@pytest.mark.django_db
def test_unread_filter():
    user = UserFactory(email="unread@example.com")
    NotificationFactory(user=user, is_read=True)
    NotificationFactory(user=user, is_read=False)

    client = authed_client(user)
    response = client.get(reverse("notification-list"), {"unread": "true"})
    assert response.data["count"] == 1
    assert response.data["results"][0]["is_read"] is False


@pytest.mark.django_db
def test_mark_single_notification_read():
    user = UserFactory(email="marker@example.com")
    notification = NotificationFactory(user=user, is_read=False)

    client = authed_client(user)
    response = client.post(reverse("notification-read", kwargs={"pk": notification.id}))
    assert response.status_code == 200

    notification.refresh_from_db()
    assert notification.is_read is True


@pytest.mark.django_db
def test_cannot_mark_someone_elses_notification_read():
    owner = UserFactory(email="realowner@example.com")
    intruder = UserFactory(email="intruder@example.com")
    notification = NotificationFactory(user=owner, is_read=False)

    client = authed_client(intruder)
    response = client.post(reverse("notification-read", kwargs={"pk": notification.id}))
    assert response.status_code == 404

    notification.refresh_from_db()
    assert notification.is_read is False


@pytest.mark.django_db
def test_mark_all_read():
    user = UserFactory(email="markall@example.com")
    NotificationFactory(user=user, is_read=False)
    NotificationFactory(user=user, is_read=False)
    NotificationFactory(user=user, is_read=True)

    client = authed_client(user)
    response = client.post(reverse("notification-read-all"))
    assert response.status_code == 200
    assert response.data["marked_read"] == 2
    assert Notification.objects.filter(user=user, is_read=False).count() == 0


@pytest.mark.django_db
def test_notifications_require_authentication():
    client = APIClient()
    response = client.get(reverse("notification-list"))
    assert response.status_code == 401


@pytest.mark.django_db(transaction=True)
def test_notify_helper_creates_notification_after_commit():
    # notify() defers via transaction.on_commit(), which only ever fires on
    # a real commit - needs transaction=True, unlike the rest of this file.
    user = UserFactory(email="notifyme@example.com")

    notify(user, "system", "Hello", "World")

    assert Notification.objects.filter(user=user, title="Hello", body="World").exists()
