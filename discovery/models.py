from django.conf import settings
from django.db import models

from catalog.models import Product
from core.models import TimeStampedModel


class RecentlyViewed(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="recently_viewed")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="viewed_by")
    viewed_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "product")
        ordering = ["-viewed_at"]

    def __str__(self):
        return f"{self.user} viewed {self.product}"


class SearchQuery(TimeStampedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="search_queries"
    )
    query_text = models.CharField(max_length=255)

    class Meta:
        indexes = [models.Index(fields=["query_text"])]
        ordering = ["-created_at"]

    def __str__(self):
        return self.query_text


class NewsletterSubscriber(TimeStampedModel):
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.email
