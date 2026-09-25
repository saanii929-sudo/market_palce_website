from celery import shared_task
from .models import DeliveryOffer
from .services import expire_offer

@shared_task
def expire_offer_task(offer_id: int) -> None:

    offer = DeliveryOffer.objects.filter(id=offer_id).first()
    if offer is not None:
        expire_offer(offer)
