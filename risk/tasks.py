from celery import shared_task
from .services import check_payment_anomaly as run


@shared_task
def check_coupon_abuse(order_id: int) -> None:

    run(order_id)


@shared_task
def check_review_farming(review_id: int) -> None:

    run(review_id)


@shared_task
def check_payment_anomaly(payment_id: int) -> None:
    

    run(payment_id)
