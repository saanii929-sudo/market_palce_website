from decimal import Decimal

from django.db import transaction

from .models import Parcel

PARCEL_DOCUMENT_FLAT_FEE = Decimal("10.00")
PARCEL_BASE_FEE = Decimal("15.00")
PARCEL_PER_KM_RATE = Decimal("3.00")


class ParcelError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def compute_parcel_price(package_size: str, distance_km) -> Decimal:
    if package_size == Parcel.PackageSize.DOCUMENT:
        return PARCEL_DOCUMENT_FLAT_FEE
    distance_km = Decimal(str(distance_km)) if distance_km is not None else Decimal("0.00")
    return (PARCEL_BASE_FEE + PARCEL_PER_KM_RATE * distance_km).quantize(Decimal("0.01"))


def get_parcel_quote(*, package_size: str, pickup_lat, pickup_lng, dropoff_lat, dropoff_lng) -> dict:
    from deliveries.services import haversine_km

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
) -> Parcel:
    from deliveries.services import create_delivery_for_parcel, haversine_km

    if pickup_address is not None:
        pickup_line1 = pickup_address.line1
        pickup_city = pickup_address.city
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
        price=price,
    )
    create_delivery_for_parcel(parcel)
    return parcel


def get_delivery_for_parcel(parcel: Parcel):
    from deliveries.services import get_delivery_for

    return get_delivery_for(parcel)


@transaction.atomic
def cancel_parcel(parcel: Parcel, user) -> Parcel:
    from deliveries.services import DispatchError, cancel_delivery

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
