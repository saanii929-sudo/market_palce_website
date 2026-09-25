import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Avg, Count, Sum
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


def get_earnings_summary(rider_profile: RiderProfile) -> dict:
    """Powers the rider app's earnings dashboard - today's numbers, this
    week's daily totals (Mon-Sun), and lifetime stats, all in one call."""
    from .models import RiderEarning

    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - datetime.timedelta(days=today_start.weekday())

    today_stats = RiderEarning.objects.filter(rider=rider_profile, created_at__gte=today_start).aggregate(
        total=Sum("total"), trip_count=Count("id"),
    )

    week_qs = RiderEarning.objects.filter(rider=rider_profile, created_at__gte=week_start)
    daily_totals = {
        row["day"]: row["total"]
        for row in week_qs.annotate(day=TruncDate("created_at")).values("day").annotate(total=Sum("total"))
    }
    week = [
        daily_totals.get((week_start + datetime.timedelta(days=i)).date(), Decimal("0.00")) for i in range(7)
    ]

    lifetime_stats = RiderEarning.objects.filter(rider=rider_profile).aggregate(
        total=Sum("total"), trip_count=Count("id"),
    )
    total_trips = lifetime_stats["trip_count"] or 0
    lifetime_total = lifetime_stats["total"] or Decimal("0.00")
    avg_per_trip = (lifetime_total / total_trips).quantize(Decimal("0.01")) if total_trips else Decimal("0.00")

    today_sessions = rider_profile.online_sessions.filter(started_at__gte=today_start)
    online_seconds = sum(
        ((session.ended_at or now) - session.started_at).total_seconds() for session in today_sessions
    )

    return {
        "available_balance": get_available_earnings_balance(rider_profile),
        "today_earnings": today_stats["total"] or Decimal("0.00"),
        "trips_today": today_stats["trip_count"] or 0,
        "online_hours_today": round(Decimal(online_seconds) / Decimal(3600), 2),
        "week": week,
        "total_trips": total_trips,
        "avg_per_trip": avg_per_trip,
    }


def get_earnings_transactions(rider_profile: RiderProfile):
    from .models import RiderEarning

    return RiderEarning.objects.filter(rider=rider_profile).select_related("trip", "trip__delivery")


def get_earnings_activity(rider_profile: RiderProfile) -> list[dict]:
    """Mixes completed deliveries and cash-outs into one feed, newest
    first - GET /riders/earnings/activity/ paginates over this list."""
    from .models import RiderEarning

    activity = []
    for earning in RiderEarning.objects.filter(rider=rider_profile).select_related("trip__delivery"):
        kind = _delivery_kind_label(earning.trip.delivery)
        activity.append({
            "type": "delivery", "label": f"{kind} delivery", "amount": earning.total,
            "created_at": earning.created_at,
        })
    for payout in RiderPayout.objects.filter(rider=rider_profile, status=RiderPayout.Status.PAID):
        activity.append({
            "type": "cash_out", "label": f"Cash out to {payout.payout_account.provider or payout.payout_account.get_type_display()}",
            "amount": -payout.amount, "created_at": payout.completed_at or payout.requested_at,
        })

    activity.sort(key=lambda row: row["created_at"], reverse=True)
    return activity


def _delivery_kind_label(delivery) -> str:
    from parcels.models import Parcel

    return "Parcel" if isinstance(delivery.content_object, Parcel) else "Store order"


def get_deliveries_history(rider_profile: RiderProfile):
    """GET /riders/deliveries/ - the rider's own completed/cancelled trips."""
    from deliveries.models import Trip
    from orders.models import SellerOrder
    from parcels.models import Parcel

    trips = (
        Trip.objects.filter(rider=rider_profile, status__in=[Trip.Status.DELIVERED, Trip.Status.CANCELLED])
        .select_related("delivery")
        .prefetch_related("delivery__content_type")
        .order_by("-created_at")
    )

    rows = []
    for trip in trips:
        delivery = trip.delivery
        obj = delivery.content_object
        if isinstance(obj, SellerOrder):
            kind, order_number_or_parcel_id = "store_order", obj.suborder_number
        elif isinstance(obj, Parcel):
            kind, order_number_or_parcel_id = "parcel", str(obj.id)
        else:
            kind, order_number_or_parcel_id = "store_order", ""

        earning = getattr(trip, "earning", None)
        rating = getattr(trip, "rating", None)
        rows.append({
            "id": trip.id,
            "kind": kind,
            "order_number_or_parcel_id": order_number_or_parcel_id,
            "pickup_label": delivery.pickup_contact_name,
            "dropoff_area": delivery.dropoff_address,
            "amount": earning.total if earning else delivery.rider_fare,
            "status": "delivered" if trip.status == Trip.Status.DELIVERED else "cancelled",
            "rating": rating.stars if rating else None,
            "completed_at": trip.delivered_at,
        })
    return rows


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


def list_payout_methods(rider_profile: RiderProfile):
    return RiderPayoutAccount.objects.filter(rider=rider_profile, is_active=True).order_by("-is_default", "-created_at")


def add_payout_method(
    rider_profile: RiderProfile, *, provider: str, account_number: str, type: str = RiderPayoutAccount.Type.MOMO,
) -> RiderPayoutAccount:
    from django.conf import settings

    from orders.services.payment_gateway import PaymentGatewayError, get_gateway

    gateway_name = getattr(settings, "PAYMENT_DEFAULT_GATEWAY", "paystack")
    try:
        reference = get_gateway(gateway_name).tokenize_payout_destination(
            type=type, account_number=account_number, account_name=rider_profile.user.full_name,
        )
    except PaymentGatewayError as exc:
        raise RiderError(str(exc)) from exc

    is_first = not RiderPayoutAccount.objects.filter(rider=rider_profile, is_active=True).exists()
    masked_number = f"•••• {account_number[-4:]}" if len(account_number) >= 4 else "••••"
    return RiderPayoutAccount.objects.create(
        rider=rider_profile, type=type, provider=provider, masked_number=masked_number,
        account_reference=reference, is_default=is_first,
    )


@transaction.atomic
def set_default_payout_method(rider_profile: RiderProfile, payout_account_id: int) -> RiderPayoutAccount:
    try:
        account = RiderPayoutAccount.objects.get(id=payout_account_id, rider=rider_profile, is_active=True)
    except RiderPayoutAccount.DoesNotExist:
        raise RiderError("Select a valid payout method.")

    RiderPayoutAccount.objects.filter(rider=rider_profile).exclude(id=account.id).update(is_default=False)
    account.is_default = True
    account.save(update_fields=["is_default"])
    return account


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


def get_reviews(rider_profile: RiderProfile):
    from .models import RiderRating

    return (
        RiderRating.objects.filter(rider=rider_profile)
        .select_related("customer")
        .order_by("-created_at")
    )


def get_reviews_summary(rider_profile: RiderProfile) -> dict:
    from .models import RiderRating

    ratings = RiderRating.objects.filter(rider=rider_profile)
    total = ratings.count()
    if not total:
        return {"average": Decimal("0.00"), "breakdown": {str(n): 0.0 for n in range(5, 0, -1)}}

    average = ratings.aggregate(avg=Avg("stars"))["avg"] or Decimal("0.00")
    counts = dict(ratings.values_list("stars").annotate(count=Count("id")))
    breakdown = {str(n): round((counts.get(n, 0) / total) * 100, 1) for n in range(5, 0, -1)}

    return {"average": Decimal(average).quantize(Decimal("0.01")), "breakdown": breakdown}
