from decimal import Decimal

import pytest

from accounts.tests.factories import UserFactory
from deliveries.models import Delivery, DeliveryOffer, Trip
from deliveries.services import accept_offer, complete_trip, confirm_pickup, submit_proof_of_delivery
from parcels.models import Parcel
from parcels.services import (
    ParcelError,
    cancel_parcel,
    compute_parcel_price,
    create_parcel,
    get_delivery_for_parcel,
    get_parcel_quote,
)
from riders.tests.factories import RiderProfileFactory


def _create_dispatched_parcel():
    """A parcel with a rider already online and in range, so create_parcel's
    call into Phase 2's dispatch produces a live pending offer."""
    rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
    sender = UserFactory()
    parcel = create_parcel(
        sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
        pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
        dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
    )
    delivery = get_delivery_for_parcel(parcel)
    offer = DeliveryOffer.objects.get(delivery=delivery)
    return parcel, delivery, offer, rider


@pytest.mark.django_db
class TestParcelPricing:
    def test_document_is_a_flat_fee_regardless_of_distance(self):
        assert compute_parcel_price(Parcel.PackageSize.DOCUMENT, None) == Decimal("10.00")
        assert compute_parcel_price(Parcel.PackageSize.DOCUMENT, 500) == Decimal("10.00")

    def test_small_medium_large_use_base_plus_per_km(self):
        price = compute_parcel_price(Parcel.PackageSize.MEDIUM, Decimal("5.00"))
        assert price == Decimal("15.00") + Decimal("3.00") * Decimal("5.00")

    def test_quote_matches_the_same_pricing_function_create_uses(self):
        quote = get_parcel_quote(
            package_size=Parcel.PackageSize.SMALL,
            pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        assert quote["distance_km"] is not None
        assert quote["price"] == compute_parcel_price(Parcel.PackageSize.SMALL, quote["distance_km"])


@pytest.mark.django_db
class TestParcelCreation:
    def test_create_parcel_creates_a_delivery_and_dispatches_it(self):
        parcel, delivery, offer, rider = _create_dispatched_parcel()

        assert delivery.delivery_type == Delivery.DeliveryType.PARCEL
        assert delivery.status == Delivery.Status.OFFERED
        assert delivery.price == parcel.price
        assert offer.rider_id == rider.id

    def test_create_parcel_requires_a_pickup_address(self):
        sender = UserFactory()
        with pytest.raises(ParcelError):
            create_parcel(
                sender, recipient_name="Jane", recipient_phone="0559998888",
                package_size=Parcel.PackageSize.SMALL, dropoff_line1="2 B St", dropoff_city="Accra",
            )

    def test_create_parcel_requires_a_dropoff_address(self):
        sender = UserFactory()
        with pytest.raises(ParcelError):
            create_parcel(
                sender, recipient_name="Jane", recipient_phone="0559998888",
                package_size=Parcel.PackageSize.SMALL, pickup_line1="1 A St", pickup_city="Accra",
                dropoff_line1="", dropoff_city="",
            )


@pytest.mark.django_db
class TestParcelStatusTracksTheUnderlyingTrip:
    def test_status_advances_through_the_full_trip_without_special_casing_the_state_machine(self):
        parcel, delivery, offer, rider = _create_dispatched_parcel()

        trip = accept_offer(offer, rider)
        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.RIDER_ASSIGNED

        trip = confirm_pickup(trip, rider)
        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.IN_TRANSIT

        pod = trip.proof_of_delivery
        submit_proof_of_delivery(trip, rider, otp_code=pod.otp_code)
        complete_trip(trip, rider)

        parcel.refresh_from_db()
        delivery.refresh_from_db()
        assert parcel.status == Parcel.Status.DELIVERED
        assert delivery.status == Delivery.Status.DELIVERED


@pytest.mark.django_db
class TestParcelCancellation:
    def test_can_cancel_while_pending_with_no_rider_assigned_yet(self):
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )  # no rider online anywhere -> Delivery stays PENDING

        cancel_parcel(parcel, sender)

        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.CANCELLED
        delivery = get_delivery_for_parcel(parcel)
        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.CANCELLED

    def test_can_cancel_while_rider_assigned_and_cancels_the_trip_too(self):
        parcel, delivery, offer, rider = _create_dispatched_parcel()
        accept_offer(offer, rider)
        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.RIDER_ASSIGNED

        cancel_parcel(parcel, parcel.sender)

        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.CANCELLED
        trip = Trip.objects.get(delivery=delivery)
        assert trip.status == Trip.Status.CANCELLED
        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.CANCELLED

    def test_cannot_cancel_after_pickup(self):
        parcel, delivery, offer, rider = _create_dispatched_parcel()
        trip = accept_offer(offer, rider)
        confirm_pickup(trip, rider)
        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.IN_TRANSIT

        with pytest.raises(ParcelError):
            cancel_parcel(parcel, parcel.sender)

    def test_only_the_sender_can_cancel(self):
        sender = UserFactory()
        other_user = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )

        with pytest.raises(ParcelError):
            cancel_parcel(parcel, other_user)

        parcel.refresh_from_db()
        assert parcel.status == Parcel.Status.PENDING
