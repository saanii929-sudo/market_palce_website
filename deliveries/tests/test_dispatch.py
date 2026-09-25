import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from deliveries.models import MAX_DISPATCH_ATTEMPTS, Delivery, DeliveryOffer
from deliveries.services import accept_offer, decline_offer, dispatch_delivery, expire_offer
from deliveries.tests.factories import DeliveryFactory
from riders.tests.factories import RiderProfileFactory


@pytest.mark.django_db
class TestDispatchCascade:
    def test_dispatch_offers_the_nearest_eligible_rider(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)

        assert offer is not None
        assert offer.rider_id == rider.id
        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.OFFERED
        assert delivery.dispatch_attempts == 1

    def test_dispatch_returns_none_when_no_rider_in_range(self):
        RiderProfileFactory(current_lat=Decimal("6.5000"), current_lng=Decimal("-0.1870"))  # far outside the search radius
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)

        assert offer is None
        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.PENDING
        assert delivery.dispatch_attempts == 1

    def test_offline_or_unverified_riders_are_never_offered(self):
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"), is_online=False)
        RiderProfileFactory(current_lat=Decimal("5.6041"), current_lng=Decimal("-0.1870"), is_verified=False)
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)

        assert offer is None

    def test_expired_offer_cascades_to_the_next_nearest_rider(self):
        far_rider = RiderProfileFactory(current_lat=Decimal("5.6200"), current_lng=Decimal("-0.1870"))
        near_rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        first_offer = dispatch_delivery(delivery)
        assert first_offer.rider_id == near_rider.id

        first_offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        first_offer.save(update_fields=["expires_at"])
        expire_offer(first_offer)

        first_offer.refresh_from_db()
        assert first_offer.status == DeliveryOffer.Status.EXPIRED

        second_offer = DeliveryOffer.objects.filter(
            delivery=delivery, status=DeliveryOffer.Status.PENDING
        ).first()
        assert second_offer is not None
        assert second_offer.rider_id == far_rider.id

        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.OFFERED
        assert delivery.dispatch_attempts == 2

    def test_declined_offer_cascades_to_the_next_rider(self):
        # Delivery pickup is at (5.6100, -0.1900) - rider_a is unambiguously
        # closer to it than rider_b.
        rider_a = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        rider_b = RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)
        assert offer.rider_id == rider_a.id

        decline_offer(offer, rider_a)

        offer.refresh_from_db()
        assert offer.status == DeliveryOffer.Status.DECLINED

        next_offer = DeliveryOffer.objects.filter(
            delivery=delivery, status=DeliveryOffer.Status.PENDING
        ).first()
        assert next_offer is not None
        assert next_offer.rider_id == rider_b.id

    def test_expiring_an_already_expired_offer_twice_does_not_double_dispatch(self):
        """expire_offer must be idempotent - a lazy poll-triggered expiry and
        the scheduled Celery task racing on the same stale offer should
        never both cascade a fresh dispatch."""
        RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)
        offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])

        expire_offer(offer)
        first_dispatch_attempts = Delivery.objects.get(pk=delivery.pk).dispatch_attempts

        expire_offer(offer)  # second call on the same now-EXPIRED offer
        second_dispatch_attempts = Delivery.objects.get(pk=delivery.pk).dispatch_attempts

        assert first_dispatch_attempts == second_dispatch_attempts

    def test_no_riders_available_once_max_dispatch_attempts_is_hit(self):
        delivery = DeliveryFactory()  # zero eligible riders exist at all

        for _ in range(MAX_DISPATCH_ATTEMPTS):
            assert dispatch_delivery(delivery) is None

        delivery.refresh_from_db()
        assert delivery.dispatch_attempts == MAX_DISPATCH_ATTEMPTS
        assert delivery.no_riders_available is True

        # A further attempt beyond the cap must not increment attempts again.
        dispatch_delivery(delivery)
        delivery.refresh_from_db()
        assert delivery.dispatch_attempts == MAX_DISPATCH_ATTEMPTS

    def test_a_rider_on_an_active_trip_is_never_offered_a_second_delivery(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery_1 = DeliveryFactory()
        delivery_2 = DeliveryFactory()

        offer_1 = dispatch_delivery(delivery_1)
        accept_offer(offer_1, rider)

        offer_2 = dispatch_delivery(delivery_2)

        assert offer_2 is None

    def test_status_resets_to_pending_when_a_cascade_attempt_finds_nobody(self):
        """A delivery that successfully matched once, then had that offer
        lapse with no replacement found, must not stay stuck reporting
        status='offered' - no_riders_available (status==PENDING and
        attempts>=MAX) depends on this to ever become true."""
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        first_offer = dispatch_delivery(delivery)
        assert first_offer is not None
        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.OFFERED

        # That rider is now the only one who exists, so declining removes
        # the sole eligible candidate - the cascade must find nobody.
        decline_offer(first_offer, rider)

        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.PENDING
