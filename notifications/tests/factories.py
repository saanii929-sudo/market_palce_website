import factory
from factory.django import DjangoModelFactory

from accounts.tests.factories import UserFactory
from notifications.models import Notification


class NotificationFactory(DjangoModelFactory):
    class Meta:
        model = Notification

    user = factory.SubFactory(UserFactory)
    type = Notification.Type.SYSTEM
    title = factory.Sequence(lambda n: f"Notification {n}")
    body = "Some body text."
