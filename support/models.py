from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class FAQ(TimeStampedModel):
    class Topic(models.TextChoices):
        ORDERS = "orders", "Orders"
        PAYMENTS = "payments", "Payments"
        RETURNS = "returns", "Returns"
        ACCOUNT = "account", "Account"
        SELLING = "selling", "Selling"

    question = models.CharField(max_length=255)
    answer = models.TextField()
    topic = models.CharField(max_length=20, choices=Topic.choices)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "id"]
        verbose_name = "FAQ"
        verbose_name_plural = "FAQs"

    def __str__(self):
        return self.question


class SupportTicket(TimeStampedModel):
    class Channel(models.TextChoices):
        CHAT = "chat", "Chat"
        EMAIL = "email", "Email"
        CALL = "call", "Call"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        RESOLVED = "resolved", "Resolved"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="support_tickets")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    subject = models.CharField(max_length=200)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.subject} ({self.status})"
