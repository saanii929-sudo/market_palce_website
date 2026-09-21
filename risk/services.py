import datetime

from django.contrib.contenttypes.models import ContentType
from django.db.models import F, Q
from django.utils import timezone
from orders.models import SellerOrder
from reviews.models import Review
from .models import RiskFlag
from orders.models import Order
from orders.models import Order, Payment
from accounts.models import User
from orders.models import Order
from reviews.models import Review
from orders.models import Order
from cart.models import Coupon

REVIEW_FARMING_DELIVERY_WINDOW_SECONDS = 60
REVIEW_FARMING_VOLUME_WINDOW_HOURS = 24
REVIEW_FARMING_VOLUME_THRESHOLD = 5
PAYMENT_ANOMALY_FAILURE_WINDOW_HOURS = 1
PAYMENT_ANOMALY_FAILURE_THRESHOLD = 3


class RiskFlagError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def raise_risk_flag(*, content_object, flag_type: str, score, details: dict | None = None) -> RiskFlag:
    return RiskFlag.objects.create(
        content_type=ContentType.objects.get_for_model(content_object),
        object_id=content_object.pk,
        flag_type=flag_type,
        score=score,
        details=details or {},
    )


def check_coupon_abuse(order_id: int) -> None:
    

    order = Order.objects.filter(id=order_id).select_related("coupon").first()
    if order is None or order.coupon_id is None:
        return

    other_orders = (
        Order.objects.filter(coupon_id=order.coupon_id)
        .exclude(user_id=order.user_id)
        .filter(
            Q(delivery_phone=order.delivery_phone)
            | Q(delivery_line1=order.delivery_line1, delivery_city=order.delivery_city)
        )
    )
    matches = list(other_orders.values("id", "order_number", "user_id")[:10])
    if not matches:
        return

    score = min(100, 40 + 15 * len(matches))
    raise_risk_flag(
        content_object=order,
        flag_type=RiskFlag.FlagType.COUPON_ABUSE,
        score=score,
        details={"coupon_code": order.coupon.code, "matched_orders": matches},
    )


def check_review_farming(review_id: int) -> None:

    review = Review.objects.filter(id=review_id).select_related("order_item__seller_order", "user").first()
    if review is None:
        return

    reasons = []
    score = 0

    seller_order = review.order_item.seller_order
    delivered_event = (
        seller_order.status_history.filter(status=SellerOrder.Status.DELIVERED).order_by("-created_at").first()
    )
    if delivered_event:
        seconds_since_delivery = (review.created_at - delivered_event.created_at).total_seconds()
        if 0 <= seconds_since_delivery < REVIEW_FARMING_DELIVERY_WINDOW_SECONDS:
            reasons.append(f"Posted {int(seconds_since_delivery)}s after delivery.")
            score += 50

    window_start = review.created_at - datetime.timedelta(hours=REVIEW_FARMING_VOLUME_WINDOW_HOURS)
    recent_count = Review.objects.filter(user=review.user, created_at__gte=window_start).count()
    if recent_count >= REVIEW_FARMING_VOLUME_THRESHOLD:
        reasons.append(f"{recent_count} reviews from this user in the last {REVIEW_FARMING_VOLUME_WINDOW_HOURS}h.")
        score += 30

    if not reasons:
        return

    raise_risk_flag(
        content_object=review, flag_type=RiskFlag.FlagType.REVIEW_FARMING, score=min(score, 100),
        details={"reasons": reasons},
    )


def check_payment_anomaly(payment_id: int) -> None:
    

    payment = Payment.objects.filter(id=payment_id).select_related("order__user").first()
    if payment is None or payment.status != Payment.Status.SUCCESS:
        return

    order = payment.order
    reasons = []
    score = 0

    window_start = payment.created_at - datetime.timedelta(hours=PAYMENT_ANOMALY_FAILURE_WINDOW_HOURS)
    recent_failures = Payment.objects.filter(
        order__user_id=order.user_id, status=Payment.Status.FAILED,
        created_at__gte=window_start, created_at__lt=payment.created_at,
    ).count()
    if recent_failures >= PAYMENT_ANOMALY_FAILURE_THRESHOLD:
        reasons.append(f"{recent_failures} failed payment attempts in the hour before this success.")
        score += 40

    prior_regions = set(
        Order.objects.filter(user_id=order.user_id).exclude(id=order.id).exclude(delivery_region="")
        .values_list("delivery_region", flat=True)
    )
    if prior_regions and order.delivery_region and order.delivery_region not in prior_regions:
        reasons.append(f"First order shipping to {order.delivery_region} (previously: {', '.join(sorted(prior_regions))}).")
        score += 20

    if not reasons:
        return

    raise_risk_flag(
        content_object=order, flag_type=RiskFlag.FlagType.PAYMENT_ANOMALY, score=min(score, 100),
        details={"reasons": reasons, "payment_id": payment.id},
    )


def _resolve_user_for(risk_flag: RiskFlag):
    

    obj = risk_flag.content_object
    if isinstance(obj, User):
        return obj
    if isinstance(obj, (Order, Review)):
        return obj.user
    return None


def _resolve_order_for(risk_flag: RiskFlag):
    

    obj = risk_flag.content_object
    return obj if isinstance(obj, Order) else None


def review_risk_flag(*, risk_flag: RiskFlag, admin_user, action: str = "none") -> RiskFlag:
    

    if action == "suspend_user":
        user = _resolve_user_for(risk_flag)
        if user is None:
            raise RiskFlagError("Can't determine which user to suspend from this flag.")
        user.is_active = False
        user.save(update_fields=["is_active"])
    elif action == "void_redemption":
        order = _resolve_order_for(risk_flag)
        if order is None or order.coupon_id is None:
            raise RiskFlagError("Can't determine which coupon redemption to void from this flag.")
        Coupon.objects.filter(id=order.coupon_id, times_used__gt=0).update(times_used=F("times_used") - 1)
        order.coupon = None
        order.save(update_fields=["coupon"])
    elif action != "none":
        raise RiskFlagError("Unknown action - use 'suspend_user', 'void_redemption', or 'none'.")

    risk_flag.reviewed = True
    risk_flag.reviewed_by = admin_user
    risk_flag.reviewed_at = timezone.now()
    risk_flag.action_taken = action
    risk_flag.save(update_fields=["reviewed", "reviewed_by", "reviewed_at", "action_taken"])
    return risk_flag
