import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from deliveries.services import accept_offer, decline_offer, dispatch_delivery, expire_offer
from deliveries.tests.factories import DeliveryFactory
from riders.tests.factories import RiderProfileFactory


@pytest.mark.django_db
class TestAcceptanceRate:
    def test_defaults_to_100_with_no_history(self):
        rider = RiderProfileFactory()
        assert rider.acceptance_rate == Decimal("100.00")

    def test_declining_an_offer_lowers_the_rate(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)
        decline_offer(offer, rider)

        rider.refresh_from_db()
        assert rider.acceptance_rate == Decimal("0.00")

    def test_accepting_raises_the_rate_back_up(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))

        offer_1 = dispatch_delivery(DeliveryFactory())
        decline_offer(offer_1, rider)
        rider.refresh_from_db()
        assert rider.acceptance_rate == Decimal("0.00")

        offer_2 = dispatch_delivery(DeliveryFactory())
        assert offer_2 is not None
        accept_offer(offer_2, rider)

        rider.refresh_from_db()
        assert rider.acceptance_rate == Decimal("50.00")

    def test_an_expired_unanswered_offer_counts_against_the_rate(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        delivery = DeliveryFactory()

        offer = dispatch_delivery(delivery)
        offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])
        expire_offer(offer)

        rider.refresh_from_db()
        assert rider.acceptance_rate == Decimal("0.00")

    def test_a_still_pending_offer_does_not_affect_the_rate(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6040"), current_lng=Decimal("-0.1870"))
        dispatch_delivery(DeliveryFactory())

        rider.refresh_from_db()
        assert rider.acceptance_rate == Decimal("100.00")
