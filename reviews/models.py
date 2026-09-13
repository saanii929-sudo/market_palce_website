from django.conf import settings
from django.db import models

from catalog.models import Product
from core.models import TimeStampedModel
from orders.models import OrderItem


class Review(TimeStampedModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="reviews")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="reviews")
    order_item = models.OneToOneField(OrderItem, on_delete=models.CASCADE, related_name="review")
    rating = models.PositiveSmallIntegerField()
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(rating__gte=1) & models.Q(rating__lte=5), name="review_rating_range"
            )
        ]

    def __str__(self):
        return f"{self.user} rated {self.product} {self.rating}/5"
