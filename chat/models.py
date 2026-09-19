from django.conf import settings
from django.db import models

from catalog.models import Seller
from core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    class Kind(models.TextChoices):
        CUSTOMER_SELLER = "customer_seller", "Customer ↔ Seller"
        CUSTOMER_SUPPORT = "customer_support", "Customer ↔ Support"
        SELLER_SUPPORT = "seller_support", "Seller ↔ Support"

    kind = models.CharField(max_length=20, choices=Kind.choices)
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True,
        related_name="customer_conversations",
    )
    seller = models.ForeignKey(
        Seller, on_delete=models.CASCADE, null=True, blank=True, related_name="conversations"
    )

    last_message_at = models.DateTimeField(null=True, blank=True)
    customer_last_read_at = models.DateTimeField(null=True, blank=True)
    seller_last_read_at = models.DateTimeField(null=True, blank=True)
    support_last_read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "seller"],
                condition=models.Q(kind="customer_seller"),
                name="unique_customer_seller_conversation",
            ),
            models.UniqueConstraint(
                fields=["customer"],
                condition=models.Q(kind="customer_support"),
                name="unique_customer_support_conversation",
            ),
            models.UniqueConstraint(
                fields=["seller"],
                condition=models.Q(kind="seller_support"),
                name="unique_seller_support_conversation",
            ),
        ]

    def __str__(self):
        if self.kind == self.Kind.CUSTOMER_SELLER:
            return f"{self.customer} ↔ {self.seller.business_name}"
        party = self.customer or self.seller.business_name
        return f"{party} ↔ Support"


class Message(TimeStampedModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="chat_messages")
    body = models.TextField(max_length=4000)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender}: {self.body[:40]}"
