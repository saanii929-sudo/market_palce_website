import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from .models import (
    REQUIRED_DOCUMENTS_BY_VEHICLE_TYPE,
    RiderDocument,
    RiderPayout,
    RiderPayoutAccount,
    RiderProfile,
    Vehicle,
)


class RiderError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@transaction.atomic
def register_rider(
    *,
    email: str = "",
    phone: str = "",
    full_name: str = "",
    password: str,
    vehicle_type: str,
    vehicle_make: str = "",
    vehicle_model: str = "",
    plate_number: str = "",
    color: str = "",
) -> RiderProfile:
    from accounts.models import User

    if not email and not phone:
        raise RiderError("Provide at least an email or a phone number.")

    user = User(email=email or None, phone=phone or None, full_name=full_name, role=User.Role.RIDER)
    user.set_password(password)
    user.save()

    rider_profile = RiderProfile.objects.create(user=user)
    Vehicle.objects.create(
        rider=rider_profile, type=vehicle_type, make=vehicle_make, model=vehicle_model,
        plate_number=plate_number, color=color,
    )
    return rider_profile


def current_vehicle(rider_profile: RiderProfile) -> Vehicle | None:
    return rider_profile.vehicles.order_by("-created_at").first()


def required_document_types(rider_profile: RiderProfile) -> list[str]:
    vehicle = current_vehicle(rider_profile)
    if vehicle is None:
        return []
    return REQUIRED_DOCUMENTS_BY_VEHICLE_TYPE.get(vehicle.type, [])


def recompute_verification(rider_profile: RiderProfile) -> bool:
    required = required_document_types(rider_profile)
    if not required:
        is_verified = False
    else:
        verified_types = set(
            rider_profile.documents.filter(status=RiderDocument.Status.VERIFIED).values_list("doc_type", flat=True)
        )
        is_verified = set(required).issubset(verified_types)

    if rider_profile.is_verified != is_verified:
        rider_profile.is_verified = is_verified
        rider_profile.save(update_fields=["is_verified"])
    return is_verified


@transaction.atomic
def upload_document(rider_profile: RiderProfile, *, doc_type: str, file, expires_at=None) -> RiderDocument:
    document, _ = RiderDocument.objects.update_or_create(
        rider=rider_profile, doc_type=doc_type,
        defaults={
            "file": file, "status": RiderDocument.Status.PENDING, "expires_at": expires_at,
            "reviewer_note": "", "reviewed_at": None,
        },
    )
    recompute_verification(rider_profile)
    return document


@transaction.atomic
def review_document(document: RiderDocument, *, action: str, reviewer_note: str = "") -> RiderDocument:
    if action == "verify":
        document.status = RiderDocument.Status.VERIFIED
    elif action == "reject":
        document.status = RiderDocument.Status.REJECTED
    else:
        raise RiderError("Unknown action - use 'verify' or 'reject'.")

    document.reviewer_note = reviewer_note
    document.reviewed_at = timezone.now()
    document.save(update_fields=["status", "reviewer_note", "reviewed_at"])

    recompute_verification(document.rider)
    return document


def set_online(rider_profile: RiderProfile, is_online: bool) -> RiderProfile:
    try:
        rider_profile.set_online(is_online)
    except DjangoValidationError as exc:
        raise RiderError(exc.message) from exc
    return rider_profile


def upsert_vehicle(rider_profile: RiderProfile, **fields) -> Vehicle:
    vehicle = current_vehicle(rider_profile)
    if vehicle is None:
        return Vehicle.objects.create(rider=rider_profile, **fields)
    for key, value in fields.items():
        setattr(vehicle, key, value)
    vehicle.save()
    return vehicle


def update_rider_settings(rider_profile: RiderProfile, data: dict) -> RiderProfile:
    if "min_trip_value" in data:
        rider_profile.min_trip_value = data["min_trip_value"]
        rider_profile.save(update_fields=["min_trip_value"])

    user = rider_profile.user
    user_fields = [
        field for field in ("push_notifications_enabled", "email_offers_enabled") if field in data
    ]
    if user_fields:
        for field in user_fields:
            setattr(user, field, data[field])
        user.save(update_fields=user_fields)

    return rider_profile


def credit_trip_earnings(trip):
    """Called from exactly one place - deliveries.services.complete_trip.
    Copies the fare that was already locked on the Delivery at dispatch
    time (see Delivery.lock_rider_fare) - never recomputes it here."""
    from .models import RiderEarning

    delivery = trip.delivery
    earning, _ = RiderEarning.objects.get_or_create(
        trip=trip,
        defaults={
            "rider": trip.rider,
            "base_fare": delivery.rider_base_fare,
            "distance_bonus": delivery.rider_distance_bonus,
            "surge_multiplier": delivery.rider_surge_multiplier,
            "total": delivery.rider_fare,
        },
    )
    return earning


EARNINGS_PERIODS = {"today", "week", "month"}


def get_earnings_summary(rider_profile: RiderProfile, period: str = "today") -> dict:
    from .models import RiderEarning

    if period not in EARNINGS_PERIODS:
        period = "today"

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = today_start - datetime.timedelta(days=today_start.weekday())
    elif period == "month":
        start = today_start.replace(day=1)
    else:
        start = today_start

    qs = RiderEarning.objects.filter(rider=rider_profile, created_at__gte=start)
    stats = qs.aggregate(total=Sum("total"), trip_count=Count("id"))
    daily_breakdown = (
        qs.annotate(day=TruncDate("created_at")).values("day").annotate(total=Sum("total")).order_by("day")
    )

    return {
        "period": period,
        "start_date": start.date(),
        "total_earnings": stats["total"] or Decimal("0.00"),
        "trip_count": stats["trip_count"] or 0,
        "daily_breakdown": [{"date": row["day"], "total": row["total"]} for row in daily_breakdown],
    }


def get_earnings_transactions(rider_profile: RiderProfile):
    from .models import RiderEarning

    return RiderEarning.objects.filter(rider=rider_profile).select_related("trip", "trip__delivery")


def get_available_earnings_balance(rider_profile: RiderProfile) -> Decimal:
    from .models import RiderEarning

    lifetime = (
        RiderEarning.objects.filter(rider=rider_profile).aggregate(total=Sum("total"))["total"] or Decimal("0.00")
    )
    committed = RiderPayout.objects.filter(
        rider=rider_profile,
        status__in=[RiderPayout.Status.REQUESTED, RiderPayout.Status.SCHEDULED, RiderPayout.Status.PAID],
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    return max(lifetime - committed, Decimal("0.00"))


def request_payout(rider_profile: RiderProfile, *, amount: Decimal, payout_account: RiderPayoutAccount) -> RiderPayout:
    from .notifications import notify_rider_payout_requested

    if not rider_profile.is_verified:
        raise RiderError("Complete verification before requesting a payout.")
    if payout_account.rider_id != rider_profile.id or not payout_account.is_active:
        raise RiderError("Select a valid payout account.")
    if amount is None or amount <= 0:
        raise RiderError("Enter a valid amount to withdraw.")
    if RiderPayout.objects.filter(rider=rider_profile, status=RiderPayout.Status.REQUESTED).exists():
        raise RiderError("You already have a withdrawal request awaiting review.")

    available = get_available_earnings_balance(rider_profile)
    if amount > available:
        raise RiderError(f"You can withdraw up to GH₵{available}.")

    payout = RiderPayout.objects.create(rider=rider_profile, amount=amount, payout_account=payout_account)
    notify_rider_payout_requested(payout)
    return payout


@transaction.atomic
def schedule_rider_payout(payout: RiderPayout, admin_note: str = "") -> RiderPayout:
    from .notifications import notify_rider_payout_resolved

    if payout.status != RiderPayout.Status.REQUESTED:
        raise RiderError("Only requested payouts can be scheduled.")

    payout.status = RiderPayout.Status.SCHEDULED
    payout.admin_note = admin_note
    payout.save(update_fields=["status", "admin_note"])
    notify_rider_payout_resolved(payout)
    return payout


@transaction.atomic
def mark_rider_payout_paid(payout: RiderPayout) -> RiderPayout:
    from .notifications import notify_rider_payout_resolved

    if payout.status not in (RiderPayout.Status.REQUESTED, RiderPayout.Status.SCHEDULED):
        raise RiderError("Only requested or scheduled payouts can be marked as paid.")
    if not RiderPayoutAccount.objects.filter(rider=payout.rider, is_active=True).exists():
        raise RiderError("This rider hasn't completed KYC verification - no payout destination on file.")

    payout.status = RiderPayout.Status.PAID
    payout.completed_at = timezone.now()
    payout.save(update_fields=["status", "completed_at"])
    notify_rider_payout_resolved(payout)
    return payout


@transaction.atomic
def reject_rider_payout(payout: RiderPayout, admin_note: str = "") -> RiderPayout:
    from .notifications import notify_rider_payout_resolved

    if payout.status != RiderPayout.Status.REQUESTED:
        raise RiderError("Only requested payouts can be rejected.")

    payout.status = RiderPayout.Status.REJECTED
    payout.admin_note = admin_note
    payout.save(update_fields=["status", "admin_note"])
    notify_rider_payout_resolved(payout)
    return payout
