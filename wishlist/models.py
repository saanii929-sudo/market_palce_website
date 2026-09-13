from django.conf import settings
from django.db import models

from catalog.models import Product
from core.models import TimeStampedModel


class WishlistItem(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="wishlist_items"
    )
    session_key = models.CharField(max_length=40, null=True, blank=True)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="wishlisted_by")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "product"], condition=models.Q(user__isnull=False), name="unique_wishlist_item_per_user"
            ),
            models.UniqueConstraint(
                fields=["session_key", "product"],
                condition=models.Q(session_key__isnull=False),
                name="unique_wishlist_item_per_session",
            ),
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(session_key__isnull=False),
                name="wishlist_item_must_have_user_or_session",
            ),
        ]

    def __str__(self):
        return f"{self.user or self.session_key} ♥ {self.product}"
