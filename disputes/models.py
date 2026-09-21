from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Dispute(TimeStampedModel):
    class Category(models.TextChoices):
        ITEM_NOT_AS_DESCRIBED = "item_not_as_described", "Item not as described"
        NO_DELIVERY = "no_delivery", "No delivery"
        REFUND_DISAGREEMENT = "refund_disagreement", "Refund disagreement"
        OTHER = "other", "Other"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        INVESTIGATING = "investigating", "Investigating"
        RESOLVED_BUYER_FAVOR = "resolved_buyer_favor", "Resolved - buyer favor"
        RESOLVED_SELLER_FAVOR = "resolved_seller_favor", "Resolved - seller favor"
        RESOLVED_PARTIAL = "resolved_partial", "Resolved - partial"

    RESOLVED_STATUSES = {Status.RESOLVED_BUYER_FAVOR, Status.RESOLVED_SELLER_FAVOR, Status.RESOLVED_PARTIAL}

    refund_request = models.ForeignKey(
        "orders.RefundRequest", on_delete=models.SET_NULL, null=True, blank=True, related_name="disputes"
    )
    order = models.ForeignKey("orders.Order", on_delete=models.CASCADE, related_name="disputes")
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="disputes_raised")
    against = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="disputes_against"
    )
    category = models.CharField(max_length=30, choices=Category.choices)
    description = models.TextField(blank=True)
    evidence = models.JSONField(default=list, blank=True, help_text="List of uploaded file/link URLs.")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.OPEN)
    assigned_admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="assigned_disputes"
    )
    resolution_note = models.CharField(max_length=500, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Dispute #{self.pk} ({self.status})"

    @property
    def is_resolved(self) -> bool:
        return self.status in self.RESOLVED_STATUSES


class DisputeMessage(TimeStampedModel):
    dispute = models.ForeignKey(Dispute, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dispute_messages")
    message = models.TextField(blank=True)
    attachments = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"Message on dispute #{self.dispute_id} by {self.sender_id}"
