from django.conf import settings
from django.db import models

from catalog.models import Product
from core.models import TimeStampedModel
from orders.models import OrderItem


class Review(TimeStampedModel):
    class Status(models.TextChoices):
        VISIBLE = "visible", "Visible"
        FLAGGED = "flagged", "Flagged"
        REMOVED = "removed", "Removed"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    order_item = models.OneToOneField(OrderItem, on_delete=models.CASCADE, related_name="review")
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.VISIBLE)
    flagged_count = models.PositiveIntegerField(default=0)
    moderation_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5), name="review_rating_range"
            )
        ]

    def __str__(self):
        return f"{self.user} rated {self.product} {self.rating}/5"


class ReviewFlag(TimeStampedModel):
    class Reason(models.TextChoices):
        SPAM = "spam", "Spam"
        FAKE = "fake", "Fake"
        OFFENSIVE = "offensive", "Offensive"
        OTHER = "other", "Other"

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="flags")
    flagged_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="review_flags")
    reason = models.CharField(max_length=20, choices=Reason.choices)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["review", "flagged_by"], name="unique_review_flag_per_user")
        ]

    def __str__(self):
        return f"Flag on review #{self.review_id} ({self.reason})"
