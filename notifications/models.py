from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Notification(TimeStampedModel):
    class Type(models.TextChoices):
        ORDER_UPDATE = "order_update", "Order update"
        PROMO = "promo", "Promotion"
        SYSTEM = "system", "System"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    type = models.CharField(max_length=20, choices=Type.choices)
    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    is_read = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "is_read"])]

    def __str__(self):
        return f"{self.type}: {self.title}"


class Broadcast(TimeStampedModel):
    """An admin-authored message fanned out as one Notification per matching
    user - see notifications.tasks.send_broadcast. Never sent inline on the
    request path, since the audience can be the whole user base."""

    class Audience(models.TextChoices):
        ALL = "all", "All users"
        CUSTOMERS = "customers", "Customers"
        SELLERS = "sellers", "Sellers"

    title = models.CharField(max_length=150)
    body = models.TextField(blank=True)
    audience = models.CharField(max_length=20, choices=Audience.choices, default=Audience.ALL)
    scheduled_for = models.DateTimeField(
        null=True, blank=True, help_text="Leave blank to send immediately on creation."
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="broadcasts_created"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title

    @property
    def is_sent(self) -> bool:
        return self.sent_at is not None


class DeviceToken(TimeStampedModel):
    """An FCM registration token for one of a user's devices - the Flutter
    app registers/refreshes this on login and whenever Firebase rotates the
    token. A token can outlive the user who registered it moving on (device
    resold, app reinstalled under a different account), so re-registering
    an existing token reassigns it rather than erroring."""

    class Platform(models.TextChoices):
        ANDROID = "android", "Android"
        IOS = "ios", "iOS"
        WEB = "web", "Web"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="device_tokens")
    token = models.CharField(max_length=255, unique=True)
    platform = models.CharField(max_length=20, choices=Platform.choices, default=Platform.ANDROID)
    is_active = models.BooleanField(default=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-last_seen_at"]
        indexes = [models.Index(fields=["user", "is_active"])]

    def __str__(self):
        return f"{self.user} ({self.platform})"
