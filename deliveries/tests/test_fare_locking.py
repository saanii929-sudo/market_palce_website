from decimal import Decimal

import pytest

from deliveries.models import RIDER_COMMISSION_RATE, RIDER_MINIMUM_FARE
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

    def test_fare_locks_on_first_dispatch_as_a_split_of_the_delivery_price(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(price=Decimal("20.00"))

        dispatch_delivery(delivery)
        delivery.refresh_from_db()

        expected_total = (delivery.price * RIDER_COMMISSION_RATE).quantize(Decimal("0.01"))
        assert expected_total > RIDER_MINIMUM_FARE  # sanity check on the fixture, not the code under test

        assert delivery.rider_base_fare == expected_total
        assert delivery.rider_distance_bonus == Decimal("0.00")
        assert delivery.rider_surge_multiplier == Decimal("1.00")
        assert delivery.rider_fare == expected_total

    def test_fare_never_falls_below_the_minimum_on_a_cheap_delivery(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(price=Decimal("2.00"))  # commission alone (0.75 * 2.00 = 1.50) is below the floor

        dispatch_delivery(delivery)
        delivery.refresh_from_db()

        assert delivery.rider_fare == RIDER_MINIMUM_FARE

    def test_fare_is_never_recomputed_once_locked(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(price=Decimal("20.00"))

        dispatch_delivery(delivery)
        delivery.refresh_from_db()
        locked_fare = delivery.rider_fare
        assert locked_fare is not None

        # Simulate the delivery price/distance shifting after the rider was
        # already shown a price - lock_rider_fare() must be a no-op now.
        delivery.price = Decimal("500.00")
        delivery.save(update_fields=["price"])
        delivery.lock_rider_fare()
        delivery.refresh_from_db()

        assert delivery.rider_fare == locked_fare

    def test_fare_stays_locked_across_a_redispatch_to_a_different_rider(self):
        # Delivery pickup is at (5.6100, -0.1900) - rider_a is unambiguously
        # closer to it than rider_b.
        rider_a = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        rider_b = RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory(price=Decimal("20.00"))

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
        delivery = DeliveryFactory(price=Decimal("20.00"))
        delivery.lock_rider_fare()
        first_fare = delivery.rider_fare

        delivery.lock_rider_fare()
        delivery.lock_rider_fare()

        assert delivery.rider_fare == first_fare
