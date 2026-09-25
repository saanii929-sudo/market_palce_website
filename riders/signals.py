from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import RiderProfile, RiderRating


def recalculate_rider_rating(rider_id: int) -> None:
    avg_rating = RiderRating.objects.filter(rider_id=rider_id).aggregate(avg=Avg("stars"))["avg"] or Decimal("0.00")
    RiderProfile.objects.filter(id=rider_id).update(
        rating_avg=Decimal(avg_rating).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


@receiver(post_save, sender=RiderRating)
@receiver(post_delete, sender=RiderRating)
def update_rider_rating_on_change(sender, instance, **kwargs):
    recalculate_rider_rating(instance.rider_id)
