from decimal import Decimal

import pytest

from deliveries.models import RIDER_BASE_FARE, RIDER_PER_KM_RATE
from deliveries.services import decline_offer, dispatch_delivery
from deliveries.tests.factories import DeliveryFactory
from riders.tests.factories import RiderProfileFactory


@pytest.mark.django_db
class TestFareLocking:
    def test_fare_is_unset_until_first_dispatch(self):
        delivery = DeliveryFactory()
        assert delivery.rider_fare is None
        assert delivery.rider_base_fare is None
        assert delivery.rider_distance_bonus is None

    def test_fare_locks_on_first_dispatch(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(distance_km=Decimal("2.50"))

        dispatch_delivery(delivery)
        delivery.refresh_from_db()

        expected_bonus = (RIDER_PER_KM_RATE * delivery.distance_km).quantize(Decimal("0.01"))
        expected_total = (RIDER_BASE_FARE + expected_bonus).quantize(Decimal("0.01"))

        assert delivery.rider_base_fare == RIDER_BASE_FARE
        assert delivery.rider_distance_bonus == expected_bonus
        assert delivery.rider_surge_multiplier == Decimal("1.00")
        assert delivery.rider_fare == expected_total

    def test_fare_is_never_recomputed_once_locked(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(distance_km=Decimal("1.00"))

        dispatch_delivery(delivery)
        delivery.refresh_from_db()
        locked_fare = delivery.rider_fare
        assert locked_fare is not None

        # Simulate the distance estimate shifting after the rider was
        # already shown a price - lock_rider_fare() must be a no-op now.
        delivery.distance_km = Decimal("500.00")
        delivery.save(update_fields=["distance_km"])
        delivery.lock_rider_fare()
        delivery.refresh_from_db()

        assert delivery.rider_fare == locked_fare

    def test_fare_stays_locked_across_a_redispatch_to_a_different_rider(self):
        # Delivery pickup is at (5.6100, -0.1900) - rider_a is unambiguously
        # closer to it than rider_b.
        rider_a = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        rider_b = RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(distance_km=Decimal("1.00"))

        first_offer = dispatch_delivery(delivery)
        delivery.refresh_from_db()
        locked_fare = delivery.rider_fare
        assert first_offer.rider_id == rider_a.id

        decline_offer(first_offer, rider_a)
        delivery.refresh_from_db()

        assert delivery.rider_fare == locked_fare
        second_offer = delivery.offers.filter(rider=rider_b).first()
        assert second_offer is not None
        assert second_offer.status == "pending"

    def test_fare_locking_is_idempotent_across_repeated_calls(self):
        delivery = DeliveryFactory(distance_km=Decimal("1.00"))
        delivery.lock_rider_fare()
        first_fare = delivery.rider_fare

        delivery.lock_rider_fare()
        delivery.lock_rider_fare()

        assert delivery.rider_fare == first_fare
