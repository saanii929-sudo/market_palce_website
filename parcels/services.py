from decimal import Decimal
from django.db import transaction
from orders.services.payment_gateway import HubtelGateway, PaymentGatewayError
from .models import PackageSizePricing, Parcel
from deliveries.services import create_delivery_for_parcel, haversine_km
from deliveries.services import get_delivery_for
from deliveries.models import Delivery
from deliveries.services import build_tracking_payload, dispatch_delivery
from deliveries.services import DispatchError, cancel_delivery

# ---------------------------------------------------------------------------
# Hard-coded fallback constants — used only when no active PackageSizePricing
# row exists in the DB for a given size.  Once the admin seeds all four rows
# these constants become dormant.
# ---------------------------------------------------------------------------
_FALLBACK_DOCUMENT_FLAT_FEE = Decimal("15.00")
_FALLBACK_BASE_FEE = Decimal("15.00")
_FALLBACK_PER_KM_RATE = Decimal("3.00")

# Keep the old names around so any external import that references them
# still works without a breaking change.
PARCEL_DOCUMENT_FLAT_FEE = _FALLBACK_DOCUMENT_FLAT_FEE
PARCEL_BASE_FEE = _FALLBACK_BASE_FEE
PARCEL_PER_KM_RATE = _FALLBACK_PER_KM_RATE


class ParcelError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _get_pricing_rule(package_size: str) -> PackageSizePricing | None:
    """Return the most-recently-updated active pricing row for *package_size*,
    or None if no row has been seeded yet."""
    return (
        PackageSizePricing.objects
        .filter(size=package_size, is_active=True)
        .order_by("-updated_at")
        .first()
    )


def compute_parcel_price(package_size: str, distance_km) -> Decimal:
    """Return the fare for *package_size* over *distance_km* kilometres.

    Lookup order:
    1. Active PackageSizePricing row in DB  →  uses row.compute_price()
    2. Hard-coded fallback constants         →  same formula as before
    """
    rule = _get_pricing_rule(package_size)
    if rule is not None:
        return rule.compute_price(distance_km)

    # --- fallback (no DB row yet) ---
    if package_size == Parcel.PackageSize.DOCUMENT:
        return _FALLBACK_DOCUMENT_FLAT_FEE
    dist = Decimal(str(distance_km)) if distance_km is not None else Decimal("0.00")
    return (_FALLBACK_BASE_FEE + _FALLBACK_PER_KM_RATE * dist).quantize(Decimal("0.01"))


def get_parcel_quote(*, package_size: str, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng) -> dict:
    distance_km = None
    if None not in (pickup_lat, pickup_lng, dropoff_lat, dropoff_lng):
        distance_km = round(
            haversine_km(float(pickup_lat), float(pickup_lng), float(dropoff_lat), float(dropoff_lng)), 2
        )
    return {"distance_km": distance_km, "price": compute_parcel_price(package_size, distance_km)}


@transaction.atomic
def create_parcel(
    sender, *, recipient_name: str, recipient_phone: str, package_size: str,
    dropoff_line1: str, dropoff_city: str,
    pickup_address=None, pickup_line1: str = "", pickup_city: str = "",
    pickup_lat=None, pickup_lng=None, dropoff_lat=None, dropoff_lng=None,
    description: str = "", photo=None, declared_value=None,
    payment_method: str = Parcel.PaymentMethod.ONLINE,
) -> Parcel:

    if pickup_address is not None:
        pickup_line1 = pickup_address.line1
        pickup_city = pickup_address.city
        if pickup_lat is None and pickup_lng is None:
            pickup_lat, pickup_lng = pickup_address.lat, pickup_address.lng
    if not pickup_line1 or not pickup_city:
        raise ParcelError("Provide a pickup address.")
    if not dropoff_line1 or not dropoff_city:
        raise ParcelError("Provide a dropoff address.")

    distance_km = None
    if None not in (pickup_lat, pickup_lng, dropoff_lat, dropoff_lng):
        distance_km = round(
            haversine_km(float(pickup_lat), float(pickup_lng), float(dropoff_lat), float(dropoff_lng)), 2
        )
    price = compute_parcel_price(package_size, distance_km)

    parcel = Parcel.objects.create(
        sender=sender, recipient_name=recipient_name, recipient_phone=recipient_phone,
        pickup_address=pickup_address, pickup_line1=pickup_line1, pickup_city=pickup_city,
        pickup_lat=pickup_lat, pickup_lng=pickup_lng,
        dropoff_line1=dropoff_line1, dropoff_city=dropoff_city, dropoff_lat=dropoff_lat, dropoff_lng=dropoff_lng,
        package_size=package_size, description=description, photo=photo, declared_value=declared_value,
        price=price, payment_method=payment_method,
    )
    create_delivery_for_parcel(parcel, dispatch=False)
    return parcel


def get_delivery_for_parcel(parcel: Parcel):
    return get_delivery_for(parcel)


def initiate_parcel_checkout(
    parcel: Parcel, user, *, callback_url: str, return_url: str, cancellation_url: str
) -> Parcel:

    if parcel.sender_id != user.id:
        raise ParcelError("This isn't your package.")
    if parcel.payment_method != Parcel.PaymentMethod.ONLINE:
        raise ParcelError("This package is set to pay cash on pickup.")
    if parcel.payment_status == Parcel.PaymentStatus.PAID:
        raise ParcelError("This package has already been paid for.")

    try:
        result = HubtelGateway().initiate_checkout(
            reference=parcel.payment_reference,
            amount=parcel.price,
            description=f"SportShop parcel delivery #{parcel.pk}",
            callback_url=callback_url,
            return_url=return_url,
            cancellation_url=cancellation_url,
        )
    except PaymentGatewayError as exc:
        raise ParcelError(str(exc)) from exc

    parcel.checkout_url = result["authorization_url"] or ""
    parcel.save(update_fields=["checkout_url"])
    return parcel


def check_and_finalize_parcel_payment(parcel: Parcel) -> Parcel:

    if parcel.payment_status != Parcel.PaymentStatus.UNPAID:
        return parcel

    try:
        result = HubtelGateway().check_status(parcel.payment_reference)
    except PaymentGatewayError as exc:
        raise ParcelError(str(exc)) from exc

    if result["status"] == "success":
        parcel.payment_status = Parcel.PaymentStatus.PAID
        parcel.save(update_fields=["payment_status"])
    elif result["status"] == "failed":
        parcel.payment_status = Parcel.PaymentStatus.FAILED
        parcel.save(update_fields=["payment_status"])
    return parcel


@transaction.atomic
def find_rider_for_parcel(parcel: Parcel, user) -> dict:

    if parcel.sender_id != user.id:
        raise ParcelError("This isn't your package.")
    if (
        parcel.payment_method == Parcel.PaymentMethod.ONLINE
        and parcel.payment_status != Parcel.PaymentStatus.PAID
    ):
        raise ParcelError("Please complete payment before we can find you a rider.")
    if parcel.status != Parcel.Status.PENDING:
        raise ParcelError("This package already has a rider assigned, or is no longer searching.")

    delivery = get_delivery_for_parcel(parcel)
    if delivery is None:
        raise ParcelError("No delivery record found for this package.")

    # Coordinates are required for matching. Fail loudly here rather than
    # letting find_nearest_eligible_rider silently return None - the
    # customer's screen would just spin forever with no actionable feedback.
    if delivery.pickup_lat is None or delivery.pickup_lng is None:
        raise ParcelError(
            "We couldn't get your pickup location. Please re-enter your pickup address with a pin."
        )

    if delivery.status == Delivery.Status.OFFERED and not delivery.no_riders_available:
        raise ParcelError("A rider offer is already in progress. Please wait for a response.")

    if delivery.no_riders_available:
        delivery.dispatch_attempts = 0
        delivery.save(update_fields=["dispatch_attempts"])

    dispatch_delivery(delivery)
    delivery.refresh_from_db()
    return build_tracking_payload(delivery)


@transaction.atomic
def cancel_parcel(parcel: Parcel, user) -> Parcel:
    

    if parcel.sender_id != user.id:
        raise ParcelError("This isn't your package.")
    if not parcel.can_cancel():
        raise ParcelError("This package can no longer be cancelled.")

    delivery = get_delivery_for_parcel(parcel)
    if delivery is not None:
        try:
            cancel_delivery(delivery, note="Cancelled by sender.")
        except DispatchError as exc:
            raise ParcelError(exc.message) from exc

    parcel.status = Parcel.Status.CANCELLED
    parcel.save(update_fields=["status"])
    return parcel
