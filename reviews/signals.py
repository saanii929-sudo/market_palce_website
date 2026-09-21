from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg, Count
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from catalog.models import Product

from .models import Review


def recalculate_product_rating(product_id: int) -> None:
    stats = (
        Review.objects.filter(product_id=product_id)
        .exclude(status=Review.Status.REMOVED)
        .aggregate(avg=Avg("rating"), count=Count("id"))
    )
    avg_rating = stats["avg"] or Decimal("0.00")
    Product.objects.filter(id=product_id).update(
        avg_rating=Decimal(avg_rating).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        review_count=stats["count"],
    )


@receiver(post_save, sender=Review)
@receiver(post_delete, sender=Review)
def update_product_rating_on_review_change(sender, instance, **kwargs):
    recalculate_product_rating(instance.product_id)
