from celery import shared_task
from django.db.models import Q
from django.utils import timezone


@shared_task
def deactivate_expired_flash_deals():
    from .models import FlashDeal

    updated = FlashDeal.objects.filter(is_active=True).filter(
        Q(ends_at__lte=timezone.now()) | Q(stock_qty=0)
    ).update(is_active=False)
    return updated
