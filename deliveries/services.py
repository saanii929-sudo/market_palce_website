import datetime
import math
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from notifications.services import notify
from riders.models import RiderProfile, RiderRating
from .models import (
    MAX_DISPATCH_ATTEMPTS,
    OFFER_TTL_SECONDS,
    Delivery,
    DeliveryOffer,
    ProofOfDelivery,
    Trip,
    TripStatusHistory,
)
from django.contrib.contenttypes.models import ContentType
from sellers.models import SellerRiderBlock
from orders.models import SellerOrder
from riders.services import current_vehicle
from riders.services import credit_trip_earnings
from orders.models import SellerOrder
from parcels.models import Parcel
from sellers.models import SellerFulfillmentRating


DEFAULT_SEARCH_RADIUS_KM = 15.0
EARTH_RADIUS_KM = 6371.0


class DispatchError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class TripError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _bounding_box(lat: float, lng: float, radius_km: float):
    lat_delta = radius_km / 111.0
    lng_delta = radius_km / (111.0 * max(math.cos(math.radians(lat)), 0.01))
    return lat - lat_delta, lat + lat_delta, lng - lng_delta, lng + lng_delta


def create_delivery_for(
    content_object,
    *,
    delivery_type: str,
    pickup_address: str,
    pickup_contact_name: str,
    pickup_contact_phone: str,
    dropoff_address: str,
    dropoff_contact_name: str,
    dropoff_contact_phone: str,
    price,
    pickup_lat=None,
    pickup_lng=None,
    dropoff_lat=None,
    dropoff_lng=None,
) -> Delivery:
    distance_km = None
    if None not in (pickup_lat, pickup_lng, dropoff_lat, dropoff_lng):
        distance_km = round(
            haversine_km(float(pickup_lat), float(pickup_lng), float(dropoff_lat), float(dropoff_lng)), 2
        )

    return Delivery.objects.create(
        content_object=content_object,
        delivery_type=delivery_type,
        pickup_address=pickup_address,
        pickup_contact_name=pickup_contact_name,
        pickup_contact_phone=pickup_contact_phone,
        pickup_lat=pickup_lat,
        pickup_lng=pickup_lng,
        dropoff_address=dropoff_address,
        dropoff_contact_name=dropoff_contact_name,
        dropoff_contact_phone=dropoff_contact_phone,
        dropoff_lat=dropoff_lat,
        dropoff_lng=dropoff_lng,
        distance_km=distance_km,
        price=price,
    )


def create_delivery_for_seller_order(seller_order, *, dispatch: bool = True) -> Delivery:
    seller = seller_order.seller
    order = seller_order.order

    delivery = create_delivery_for(
        seller_order,
        delivery_type=Delivery.DeliveryType.MARKETPLACE_ORDER,
        pickup_address=seller.business_name,
        pickup_contact_name=seller.business_name,
        pickup_contact_phone=seller.support_phone,
        pickup_lat=getattr(seller, "pickup_lat", None),
        pickup_lng=getattr(seller, "pickup_lng", None),
        dropoff_address=f"{order.delivery_line1}, {order.delivery_city}",
        dropoff_contact_name=order.delivery_recipient_name,
        dropoff_contact_phone=order.delivery_phone,
        dropoff_lat=getattr(order, "delivery_lat", None),
        dropoff_lng=getattr(order, "delivery_lng", None),
        price=seller_order.delivery_fee or Decimal("0.00"),
    )
    if dispatch:
        dispatch_delivery(delivery)
    return delivery


def get_delivery_for(content_object) -> Delivery | None:
    

    content_type = ContentType.objects.get_for_model(type(content_object))
    return Delivery.objects.filter(content_type=content_type, object_id=content_object.pk).order_by("-id").first()


def create_delivery_for_parcel(parcel, *, dispatch: bool = True) -> Delivery:
    delivery = create_delivery_for(
        parcel,
        delivery_type=Delivery.DeliveryType.PARCEL,
        pickup_address=f"{parcel.pickup_line1}, {parcel.pickup_city}",
        pickup_contact_name=parcel.sender.full_name or parcel.sender.email or parcel.sender.phone,
        pickup_contact_phone=parcel.sender.phone or "",
        pickup_lat=parcel.pickup_lat,
        pickup_lng=parcel.pickup_lng,
        dropoff_address=f"{parcel.dropoff_line1}, {parcel.dropoff_city}",
        dropoff_contact_name=parcel.recipient_name,
        dropoff_contact_phone=parcel.recipient_phone,
        dropoff_lat=parcel.dropoff_lat,
        dropoff_lng=parcel.dropoff_lng,
        price=parcel.price,
    )
    if dispatch:
        dispatch_delivery(delivery)
    return delivery


VEHICLE_TYPES_BY_DELIVERY_SIZE = {
    "large": {"car", "van"},
    "medium": {"motorcycle", "car", "van"},
}
_DELIVERY_SIZE_RANK = {"small": 0, "medium": 1, "large": 2}


def required_vehicle_types_for_seller_order(seller_order) -> set[str] | None:
    sizes = set(
        seller_order.items.exclude(product__delivery_size="").values_list("product__delivery_size", flat=True)
    )
    if not sizes:
        return None
    dominant = max(sizes, key=lambda size: _DELIVERY_SIZE_RANK.get(size, 0))
    return VEHICLE_TYPES_BY_DELIVERY_SIZE.get(dominant)


def _matching_constraints_for_seller_order(seller_order) -> tuple[set[int], set[str] | None]:
    

    blocked_ids = set(SellerRiderBlock.objects.filter(seller=seller_order.seller).values_list("rider_id", flat=True))
    return blocked_ids, required_vehicle_types_for_seller_order(seller_order)


def _matching_constraints_for_delivery(delivery: Delivery) -> tuple[set[int], set[str] | None]:
    
   

    obj = delivery.content_object
    if not isinstance(obj, SellerOrder):
        return set(), None
    return _matching_constraints_for_seller_order(obj)


def find_nearest_eligible_rider(delivery: Delivery, exclude_rider_ids=None, radius_km: float = DEFAULT_SEARCH_RADIUS_KM):
    if delivery.pickup_lat is None or delivery.pickup_lng is None:
        return None

    exclude_rider_ids = set(exclude_rider_ids or [])
    blocked_ids, required_vehicle_types = _matching_constraints_for_delivery(delivery)
    exclude_rider_ids |= blocked_ids

    lat, lng = float(delivery.pickup_lat), float(delivery.pickup_lng)
    min_lat, max_lat, min_lng, max_lng = _bounding_box(lat, lng, radius_km)

    riders_on_active_trips = Trip.objects.filter(status__in=Trip.ACTIVE_STATUSES).values_list("rider_id", flat=True)

    candidates = (
        RiderProfile.objects.filter(
            is_online=True, is_verified=True,
            current_lat__gte=min_lat, current_lat__lte=max_lat,
            current_lng__gte=min_lng, current_lng__lte=max_lng,
        )
        .exclude(id__in=exclude_rider_ids)
        .exclude(id__in=riders_on_active_trips)
    )
    if required_vehicle_types:
        candidates = candidates.filter(vehicles__type__in=required_vehicle_types).distinct()

    nearest_rider, nearest_distance = None, None
    for rider in candidates:
        if rider.min_trip_value is not None and delivery.price < rider.min_trip_value:
            continue
        distance = haversine_km(lat, lng, float(rider.current_lat), float(rider.current_lng))
        if distance > radius_km:
            continue
        if nearest_distance is None or distance < nearest_distance:
            nearest_rider, nearest_distance = rider, distance

    return nearest_rider


AVERAGE_RIDER_SPEED_KMH = Decimal("25")


def list_nearby_riders_for_seller_order(seller_order, radius_km: float = DEFAULT_SEARCH_RADIUS_KM) -> list[dict]:
    

    seller = seller_order.seller
    pickup_lat, pickup_lng = getattr(seller, "pickup_lat", None), getattr(seller, "pickup_lng", None)
    if pickup_lat is None or pickup_lng is None:
        return []

    blocked_ids, required_vehicle_types = _matching_constraints_for_seller_order(seller_order)
    lat, lng = float(pickup_lat), float(pickup_lng)
    min_lat, max_lat, min_lng, max_lng = _bounding_box(lat, lng, radius_km)

    riders_on_active_trips = Trip.objects.filter(status__in=Trip.ACTIVE_STATUSES).values_list("rider_id", flat=True)
    candidates = (
        RiderProfile.objects.filter(
            is_online=True, is_verified=True,
            current_lat__gte=min_lat, current_lat__lte=max_lat,
            current_lng__gte=min_lng, current_lng__lte=max_lng,
        )
        .exclude(id__in=blocked_ids)
        .exclude(id__in=riders_on_active_trips)
    )
    if required_vehicle_types:
        candidates = candidates.filter(vehicles__type__in=required_vehicle_types).distinct()

    results = []
    for rider in candidates:
        distance = haversine_km(lat, lng, float(rider.current_lat), float(rider.current_lng))
        if distance > radius_km:
            continue
        vehicle = current_vehicle(rider)
        eta_minutes = int((Decimal(str(distance)) / AVERAGE_RIDER_SPEED_KMH) * 60)
        results.append({
            "rider_id": rider.id,
            "name": rider.user.full_name or rider.user.email or rider.user.phone,
            "rating": rider.rating_avg,
            "vehicle_type": vehicle.type if vehicle else None,
            "distance_km": round(distance, 2),
            "eta_minutes": eta_minutes,
        })

    results.sort(key=lambda r: r["distance_km"])
    return results


def build_delivery_request_payload(offer: DeliveryOffer) -> dict:
    

    delivery = offer.delivery
    obj = delivery.content_object

    if isinstance(obj, SellerOrder):
        kind = "store_order"
        item_count = obj.items.aggregate(total=Sum("qty"))["total"] or 0
    elif isinstance(obj, Parcel):
        kind = "parcel"
        item_count = 1
    else:
        kind = "store_order"
        item_count = 0

    distance_km = delivery.distance_km
    rider = offer.rider
    if delivery.pickup_lat is not None and delivery.pickup_lng is not None \
            and rider.current_lat is not None and rider.current_lng is not None:
        distance_km = round(
            haversine_km(
                float(rider.current_lat), float(rider.current_lng),
                float(delivery.pickup_lat), float(delivery.pickup_lng),
            ),
            2,
        )

    eta_minutes = int((Decimal(str(distance_km or 0)) / AVERAGE_RIDER_SPEED_KMH) * 60)

    return {
        "id": offer.id,
        "kind": kind,
        "pickup_label": delivery.pickup_contact_name,
        "pickup_address": delivery.pickup_address,
        "dropoff_address": delivery.dropoff_address,
        "customer_name": delivery.dropoff_contact_name,
        "amount": str(delivery.rider_fare) if delivery.rider_fare is not None else None,
        "distance_km": str(distance_km) if distance_km is not None else None,
        "eta_minutes": eta_minutes,
        "item_count": item_count,
    }


def _broadcast_offer_to_rider(offer: DeliveryOffer) -> None:
    """Best-effort, same guarantee as notifications.push: a live rider push
    must never be able to block or fail the request that created the offer.
    Runs in a background daemon thread with a hard timeout - async_to_sync
    bridging into the channel layer from inside a sync request (especially
    with an already-open WebSocket in the same ASGI process) has a known
    failure mode where it can hang instead of raising, which a plain
    try/except can't protect against."""
    import logging
    import threading

    logger = logging.getLogger(__name__)
    payload = build_delivery_request_payload(offer)
    rider_id = offer.rider_id

    def _send():
        try:
            from asgiref.sync import async_to_sync
            from channels.layers import get_channel_layer

            channel_layer = get_channel_layer()
            if channel_layer is None:
                return
            async_to_sync(channel_layer.group_send)(
                f"rider_dispatch_{rider_id}",
                {"type": "delivery.request", "request": payload},
            )
        except Exception:
            logger.exception("Failed to broadcast delivery offer to rider %s over WebSocket", rider_id)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()
    thread.join(timeout=3)
    if thread.is_alive():
        logger.warning("WebSocket broadcast to rider %s is still running after 3s - abandoning it.", rider_id)


def _notify_rider_of_offer(offer: DeliveryOffer) -> None:
    delivery = offer.delivery
    notify(
        offer.rider.user, "system", "New delivery request",
        f"GH₵{delivery.price} - pickup at {delivery.pickup_address}. You have {OFFER_TTL_SECONDS} seconds to respond.",
    )
    _broadcast_offer_to_rider(offer)


@transaction.atomic
def dispatch_delivery(delivery: Delivery) -> DeliveryOffer | None:
    delivery = Delivery.objects.select_for_update().get(pk=delivery.pk)

    if delivery.status not in (Delivery.Status.PENDING, Delivery.Status.OFFERED):
        return None
    if delivery.dispatch_attempts >= MAX_DISPATCH_ATTEMPTS:
        return None

    already_offered_rider_ids = set(delivery.offers.values_list("rider_id", flat=True))
    rider = find_nearest_eligible_rider(delivery, exclude_rider_ids=already_offered_rider_ids)

    delivery.dispatch_attempts += 1
    update_fields = ["dispatch_attempts"]

    if rider is None:
        if delivery.status != Delivery.Status.PENDING:
            delivery.status = Delivery.Status.PENDING
            update_fields.append("status")
        delivery.save(update_fields=update_fields)
        return None

    delivery.lock_rider_fare()
    offer = DeliveryOffer.objects.create(
        delivery=delivery, rider=rider, sent_at=timezone.now(),
        expires_at=timezone.now() + datetime.timedelta(seconds=OFFER_TTL_SECONDS),
    )
    delivery.status = Delivery.Status.OFFERED
    update_fields.append("status")
    delivery.save(update_fields=update_fields)

    from .tasks import expire_offer_task

    transaction.on_commit(lambda: expire_offer_task.apply_async(args=[offer.id], countdown=OFFER_TTL_SECONDS))
    transaction.on_commit(lambda: _notify_rider_of_offer(offer))

    return offer


@transaction.atomic
def dispatch_delivery_direct(delivery: Delivery, rider) -> DeliveryOffer:
    delivery = Delivery.objects.select_for_update().get(pk=delivery.pk)
    if delivery.status not in (Delivery.Status.PENDING, Delivery.Status.OFFERED):
        raise DispatchError("This delivery already has an active offer or trip.")
    if not (rider.is_online and rider.is_verified):
        raise DispatchError("That rider isn't currently available.")
    if Trip.objects.filter(rider=rider, status__in=Trip.ACTIVE_STATUSES).exists():
        raise DispatchError("That rider already has an active trip.")

    blocked_ids, required_vehicle_types = _matching_constraints_for_delivery(delivery)
    if rider.id in blocked_ids:
        raise DispatchError("This rider is blocked for this seller.")
    if required_vehicle_types and not rider.vehicles.filter(type__in=required_vehicle_types).exists():
        raise DispatchError("This rider's vehicle isn't suited for this order.")

    DeliveryOffer.objects.filter(delivery=delivery, status=DeliveryOffer.Status.PENDING).update(
        status=DeliveryOffer.Status.EXPIRED
    )

    delivery.lock_rider_fare()
    offer = DeliveryOffer.objects.create(
        delivery=delivery, rider=rider, sent_at=timezone.now(),
        expires_at=timezone.now() + datetime.timedelta(seconds=OFFER_TTL_SECONDS),
    )
    delivery.status = Delivery.Status.OFFERED
    delivery.dispatch_attempts += 1
    delivery.save(update_fields=["status", "dispatch_attempts"])

    from .tasks import expire_offer_task

    transaction.on_commit(lambda: expire_offer_task.apply_async(args=[offer.id], countdown=OFFER_TTL_SECONDS))
    transaction.on_commit(lambda: _notify_rider_of_offer(offer))

    return offer


def _notify_seller_direct_request_unfulfilled(delivery: Delivery, *, reason: str) -> None:
    user = delivery.requested_by
    if user is None:
        return
    verb = "expired without a response" if reason == "expired" else "was declined"
    notify(
        user, "system", "Rider request needs your attention",
        f"The rider you requested {verb}. Pick another rider or switch to auto-match.",
    )


def _notify_seller_of_accepted_offer(offer: DeliveryOffer) -> None:
    user = offer.delivery.requested_by
    if user is None:
        return
    rider_name = offer.rider.user.full_name or offer.rider.user.email or offer.rider.user.phone
    notify(user, "system", "Rider on the way", f"{rider_name} accepted your request and is heading to pickup.")


@transaction.atomic
def expire_offer(offer: DeliveryOffer) -> None:
    offer = DeliveryOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.status != DeliveryOffer.Status.PENDING:
        return
    if offer.expires_at > timezone.now():
        return

    offer.status = DeliveryOffer.Status.EXPIRED
    offer.save(update_fields=["status"])

    from riders.services import recompute_acceptance_rate

    recompute_acceptance_rate(offer.rider)

    delivery = offer.delivery
    if delivery.request_mode == Delivery.RequestMode.DIRECT:
        delivery.status = Delivery.Status.PENDING
        delivery.save(update_fields=["status"])
        transaction.on_commit(lambda: _notify_seller_direct_request_unfulfilled(delivery, reason="expired"))
        return

    dispatch_delivery(delivery)


def expire_due_offers_for_rider(rider_profile) -> None:
    stale_ids = list(
        DeliveryOffer.objects.filter(
            rider=rider_profile, status=DeliveryOffer.Status.PENDING, expires_at__lte=timezone.now(),
        ).values_list("id", flat=True)
    )
    for offer_id in stale_ids:
        expire_offer(DeliveryOffer.objects.get(id=offer_id))


def get_pending_offers_for_rider(rider_profile):
    expire_due_offers_for_rider(rider_profile)
    return (
        DeliveryOffer.objects.filter(
            rider=rider_profile, status=DeliveryOffer.Status.PENDING, expires_at__gt=timezone.now(),
        )
        .select_related("delivery")
        .order_by("-sent_at")
    )


@transaction.atomic
def accept_offer(offer: DeliveryOffer, rider) -> Trip:
    offer = DeliveryOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.rider_id != rider.id:
        raise DispatchError("This offer isn't yours.")
    if offer.status != DeliveryOffer.Status.PENDING:
        raise DispatchError("This offer is no longer available.")
    if offer.expires_at <= timezone.now():
        expire_offer(offer)
        raise DispatchError("This offer has expired.")

    offer.status = DeliveryOffer.Status.ACCEPTED
    offer.save(update_fields=["status"])

    from riders.services import recompute_acceptance_rate

    recompute_acceptance_rate(rider)

    DeliveryOffer.objects.filter(delivery=offer.delivery, status=DeliveryOffer.Status.PENDING).exclude(
        id=offer.id
    ).update(status=DeliveryOffer.Status.EXPIRED)

    delivery = offer.delivery
    delivery.status = Delivery.Status.ACCEPTED
    delivery.save(update_fields=["status"])

    trip = Trip.objects.create(delivery=delivery, rider=rider, status=Trip.Status.HEADING_TO_PICKUP)
    TripStatusHistory.objects.create(trip=trip, status=Trip.Status.HEADING_TO_PICKUP)
    delivery.advance_content_to_rider_assigned()

    ProofOfDelivery.objects.get_or_create(trip=trip)

    if delivery.requested_by_id:
        transaction.on_commit(lambda: _notify_seller_of_accepted_offer(offer))

    return trip


@transaction.atomic
def decline_offer(offer: DeliveryOffer, rider) -> None:
    offer = DeliveryOffer.objects.select_for_update().get(pk=offer.pk)
    if offer.rider_id != rider.id:
        raise DispatchError("This offer isn't yours.")
    if offer.status != DeliveryOffer.Status.PENDING:
        return

    offer.status = DeliveryOffer.Status.DECLINED
    offer.save(update_fields=["status"])

    from riders.services import recompute_acceptance_rate

    recompute_acceptance_rate(rider)

    delivery = offer.delivery
    if delivery.request_mode == Delivery.RequestMode.DIRECT:
        delivery.status = Delivery.Status.PENDING
        delivery.save(update_fields=["status"])
        transaction.on_commit(lambda: _notify_seller_direct_request_unfulfilled(delivery, reason="declined"))
        return

    dispatch_delivery(delivery)


@transaction.atomic
def cancel_delivery(delivery: Delivery, *, note: str = "") -> Delivery:
    delivery = Delivery.objects.select_for_update().get(pk=delivery.pk)
    if delivery.status in (Delivery.Status.DELIVERED, Delivery.Status.CANCELLED):
        raise DispatchError("This delivery can no longer be cancelled.")

    trip = getattr(delivery, "trip", None)
    if trip is not None and trip.status != Trip.Status.HEADING_TO_PICKUP:
        raise DispatchError("This delivery can no longer be cancelled - pickup is already underway.")

    DeliveryOffer.objects.filter(delivery=delivery, status=DeliveryOffer.Status.PENDING).update(
        status=DeliveryOffer.Status.EXPIRED
    )

    if trip is not None:
        trip.status = Trip.Status.CANCELLED
        trip.save(update_fields=["status"])
        TripStatusHistory.objects.create(trip=trip, status=Trip.Status.CANCELLED, note=note)

    delivery.status = Delivery.Status.CANCELLED
    delivery.save(update_fields=["status"])
    delivery.advance_content_to_cancelled()
    return delivery


@transaction.atomic
def confirm_pickup(trip: Trip, rider) -> Trip:
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.rider_id != rider.id:
        raise TripError("This trip isn't yours.")
    if trip.status != Trip.Status.HEADING_TO_PICKUP:
        raise TripError("This trip isn't awaiting pickup.")

    delivery = trip.delivery
    now = timezone.now()
    trip.status = Trip.Status.PICKED_UP
    trip.picked_up_at = now
    trip.save(update_fields=["status", "picked_up_at"])
    TripStatusHistory.objects.create(trip=trip, status=Trip.Status.PICKED_UP)
    delivery.advance_content_to_picked_up()

    trip.status = Trip.Status.HEADING_TO_DROPOFF
    trip.save(update_fields=["status"])
    TripStatusHistory.objects.create(trip=trip, status=Trip.Status.HEADING_TO_DROPOFF)
    delivery.advance_content_to_in_transit()

    ProofOfDelivery.objects.get_or_create(trip=trip)

    delivery.status = Delivery.Status.IN_PROGRESS
    delivery.save(update_fields=["status"])

    return trip


@transaction.atomic
def submit_proof_of_delivery(trip: Trip, rider, *, otp_code: str | None = None, photo=None) -> ProofOfDelivery:
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.rider_id != rider.id:
        raise TripError("This trip isn't yours.")
    if trip.status != Trip.Status.HEADING_TO_DROPOFF:
        raise TripError("This trip isn't awaiting proof of delivery.")

    pod = getattr(trip, "proof_of_delivery", None)
    if pod is None:
        raise TripError("Confirm pickup before submitting proof of delivery.")

    if photo is not None:
        pod.photo = photo
        pod.save(update_fields=["photo"])

    if otp_code is not None:
        is_match = otp_code.strip() == pod.otp_code
        pod.otp_verified = is_match
        pod.save(update_fields=["otp_verified"])
        if not is_match:
            raise TripError("That code doesn't match. Please confirm the code with the customer.")

    return pod


@transaction.atomic
def complete_trip(trip: Trip, rider) -> Trip:
    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.rider_id != rider.id:
        raise TripError("This trip isn't yours.")
    if trip.status != Trip.Status.HEADING_TO_DROPOFF:
        raise TripError("This trip can't be completed from its current state.")

    pod = getattr(trip, "proof_of_delivery", None)
    if pod is None or not pod.otp_verified:
        raise TripError("Verify the delivery code with the customer before completing this trip.")

    now = timezone.now()
    pod.delivered_at = now
    pod.save(update_fields=["delivered_at"])

    trip.status = Trip.Status.DELIVERED
    trip.delivered_at = now
    trip.save(update_fields=["status", "delivered_at"])
    TripStatusHistory.objects.create(trip=trip, status=Trip.Status.DELIVERED)

    trip.delivery.advance_content_to_delivered()

    credit_trip_earnings(trip)

    return trip


@transaction.atomic
def complete_delivery_with_code(trip: Trip, rider, *, delivery_code: str) -> Trip:
    submit_proof_of_delivery(trip, rider, otp_code=delivery_code)
    return complete_trip(trip, rider)


@transaction.atomic
def rate_trip(trip: Trip, customer, *, stars: int, comment: str = ""):
    

    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.status != Trip.Status.DELIVERED:
        raise TripError("You can only rate a trip after it's been delivered.")

    delivery_customer = trip.delivery.customer_user
    if delivery_customer is None or delivery_customer.id != customer.id:
        raise TripError("This trip isn't yours to rate.")

    if RiderRating.objects.filter(trip=trip).exists():
        raise TripError("You've already rated this trip.")

    return RiderRating.objects.create(
        trip=trip, customer=customer, rider=trip.rider, stars=stars, comment=comment,
    )


@transaction.atomic
def rate_seller_fulfillment(trip: Trip, rider, *, stars: int, comment: str = ""):


    trip = Trip.objects.select_for_update().get(pk=trip.pk)
    if trip.rider_id != rider.id:
        raise TripError("This trip isn't yours to rate.")
    if trip.status != Trip.Status.DELIVERED:
        raise TripError("You can only rate a trip after it's been delivered.")

    obj = trip.delivery.content_object
    if not isinstance(obj, SellerOrder):
        raise TripError("This trip isn't tied to a seller order.")

    if SellerFulfillmentRating.objects.filter(trip=trip).exists():
        raise TripError("You've already rated this pickup.")

    return SellerFulfillmentRating.objects.create(
        trip=trip, rider=rider, seller=obj.seller, stars=stars, comment=comment,
    )


def _delivery_kind(delivery: Delivery) -> str:
    from orders.models import SellerOrder
    from parcels.models import Parcel

    obj = delivery.content_object
    if isinstance(obj, Parcel):
        return "parcel"
    if isinstance(obj, SellerOrder):
        return "store_order"
    return "store_order"


def build_active_delivery_payload(trip: Trip) -> dict:
    delivery = trip.delivery
    return {
        "id": trip.id,
        "status": trip.status,
        "kind": _delivery_kind(delivery),
        "pickup_label": delivery.pickup_contact_name,
        "pickup_address": delivery.pickup_address,
        "dropoff_address": delivery.dropoff_address,
        "customer_name": delivery.dropoff_contact_name,
        "customer_phone": delivery.dropoff_contact_phone,
        "amount": delivery.rider_fare,
        "distance_km": delivery.distance_km,
        "picked_up_at": trip.picked_up_at,
    }


def build_tracking_payload(delivery: Delivery) -> dict:
    from riders.services import current_vehicle

    trip = getattr(delivery, "trip", None)
    rider_data = None
    delivery_otp = None
    if trip is not None:
        rider = trip.rider
        vehicle = current_vehicle(rider)
        rider_data = {
            "name": rider.user.full_name or rider.user.email or rider.user.phone,
            "rating": rider.rating_avg,
            "vehicle_type": vehicle.type if vehicle else None,
            "vehicle_plate": vehicle.plate_number if vehicle else None,
            "current_lat": rider.current_lat,
            "current_lng": rider.current_lng,
        }
        pod = getattr(trip, "proof_of_delivery", None)
        if pod is not None:
            delivery_otp = pod.otp_code

    return {
        "delivery_status": delivery.status,
        "trip_status": trip.status if trip is not None else None,
        "no_riders_available": delivery.no_riders_available,
        "rider": rider_data,
        "delivery_otp": delivery_otp,
    }
