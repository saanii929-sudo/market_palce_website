from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from core.models import TimeStampedModel


class RiskFlag(TimeStampedModel):
    class FlagType(models.TextChoices):
        COUPON_ABUSE = "coupon_abuse", "Coupon abuse"
        REVIEW_FARMING = "review_farming", "Review farming"
        MULTIPLE_ACCOUNTS = "multiple_accounts", "Multiple accounts"
        PAYMENT_ANOMALY = "payment_anomaly", "Payment anomaly"

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    flag_type = models.CharField(max_length=30, choices=FlagType.choices)
    score = models.DecimalField(max_digits=5, decimal_places=2, help_text="Higher = more suspicious.")
    details = models.JSONField(default=dict, blank=True)

    reviewed = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="reviewed_risk_flags"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    action_taken = models.CharField(max_length=30, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]

    def __str__(self):
        return f"{self.get_flag_type_display()} ({self.score}) on {self.content_type.model} #{self.object_id}"
