import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.models import Product
from catalog.tests.factories import ProductFactory, SellerFactory
from deliveries.models import Delivery, DeliveryOffer, Trip
from deliveries.services import (
    DispatchError,
    accept_offer,
    decline_offer,
    dispatch_delivery,
    dispatch_delivery_direct,
    expire_offer,
    find_nearest_eligible_rider,
    list_nearby_riders_for_seller_order,
)
from orders.models import Order, OrderItem, SellerOrder
from orders.tests.factories import DeliveryMethodFactory, PaymentMethodFactory
from riders.models import Vehicle
from riders.tests.factories import RiderProfileFactory
from sellers.models import SellerRiderBlock
from sellers.services import RiderRequestError, block_rider, request_rider_for_seller_order


def _create_seller_order(seller=None, *, delivery_size: str = "", pickup_lat=None, pickup_lng=None):
    """A real SellerOrder against the current (post order/seller-order
    split) schema - orders.tests.factories is stale against that split
    (still targets fields Order/OrderItem no longer have), so this test
    builds its own minimal fixture directly."""
    seller = seller or SellerFactory(pickup_lat=pickup_lat, pickup_lng=pickup_lng)
    product = ProductFactory(seller=seller, delivery_size=delivery_size)
    order = Order.objects.create(
        user=AddressFactory().user, payment_method=PaymentMethodFactory(),
        delivery_recipient_name="Customer", delivery_phone="0559990000",
        delivery_line1="1 Test St", delivery_city="Accra", delivery_country="Ghana",
    )
    seller_order = SellerOrder.objects.create(
        order=order, seller=seller, subtotal=Decimal("50.00"), total=Decimal("65.00"),
        delivery_fee=Decimal("15.00"), delivery_method=DeliveryMethodFactory(),
    )
    OrderItem.objects.create(seller_order=seller_order, product=product, qty=1, unit_price=Decimal("50.00"))
    return seller_order


@pytest.mark.django_db
class TestBlockListExclusion:
    """Testing priority #2 from the brief: a blocked rider must never leak
    back into a seller's matching results, whether via auto dispatch or the
    nearby-riders listing."""

    def test_blocked_rider_excluded_from_auto_dispatch(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)

        blocked_rider = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        other_rider = RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))
        SellerRiderBlock.objects.create(seller=seller, rider=blocked_rider)

        delivery = Delivery.objects.create(
            content_object=seller_order, delivery_type=Delivery.DeliveryType.MARKETPLACE_ORDER,
            pickup_address="Warehouse", pickup_contact_name="Seller", pickup_contact_phone="0550000000",
            pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_address="Customer", dropoff_contact_name="Customer", dropoff_contact_phone="0559990000",
            dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
            price=Decimal("15.00"),
        )

        rider = find_nearest_eligible_rider(delivery)

        assert rider is not None
        assert rider.id == other_rider.id
        assert rider.id != blocked_rider.id

    def test_blocked_rider_excluded_even_when_nearest(self):
        """The blocked rider being physically closest must not matter -
        exclusion happens before distance ranking."""
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)

        blocked_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))  # exact match
        SellerRiderBlock.objects.create(seller=seller, rider=blocked_rider)

        delivery = Delivery.objects.create(
            content_object=seller_order, delivery_type=Delivery.DeliveryType.MARKETPLACE_ORDER,
            pickup_address="Warehouse", pickup_contact_name="Seller", pickup_contact_phone="0550000000",
            pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_address="Customer", dropoff_contact_name="Customer", dropoff_contact_phone="0559990000",
            dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
            price=Decimal("15.00"),
        )

        assert find_nearest_eligible_rider(delivery) is None

    def test_blocked_rider_excluded_from_nearby_riders_listing(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)

        blocked_rider = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        visible_rider = RiderProfileFactory(current_lat=Decimal("5.6098"), current_lng=Decimal("-0.1900"))
        SellerRiderBlock.objects.create(seller=seller, rider=blocked_rider)

        results = list_nearby_riders_for_seller_order(seller_order)

        rider_ids = {r["rider_id"] for r in results}
        assert visible_rider.id in rider_ids
        assert blocked_rider.id not in rider_ids

    def test_blocking_a_rider_removes_them_from_favorites(self):
        from sellers.services import add_favorite_rider

        seller = SellerFactory()
        rider = RiderProfileFactory()
        add_favorite_rider(seller, rider)

        block_rider(seller, rider)

        assert not seller.favorite_riders.filter(rider=rider).exists()
        assert seller.blocked_riders.filter(rider=rider).exists()

    def test_direct_request_to_a_blocked_rider_is_rejected(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        blocked_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        SellerRiderBlock.objects.create(seller=seller, rider=blocked_rider)

        with pytest.raises(RiderRequestError):
            request_rider_for_seller_order(seller_order, seller, mode="direct", rider=blocked_rider)

    def test_vehicle_type_filtering_excludes_unsuitable_riders(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller, delivery_size=Product.DeliverySize.LARGE)

        bicycle_rider = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        bicycle_rider.vehicles.create(type=Vehicle.Type.BICYCLE, plate_number="BIKE-1")
        van_rider = RiderProfileFactory(current_lat=Decimal("5.6098"), current_lng=Decimal("-0.1900"))
        van_rider.vehicles.create(type=Vehicle.Type.VAN, plate_number="VAN-1")

        delivery = Delivery.objects.create(
            content_object=seller_order, delivery_type=Delivery.DeliveryType.MARKETPLACE_ORDER,
            pickup_address="Warehouse", pickup_contact_name="Seller", pickup_contact_phone="0550000000",
            pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_address="Customer", dropoff_contact_name="Customer", dropoff_contact_phone="0559990000",
            dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
            price=Decimal("15.00"),
        )

        rider = find_nearest_eligible_rider(delivery)

        assert rider is not None
        assert rider.id == van_rider.id


@pytest.mark.django_db
class TestDirectRequestDoesNotCascade:
    """Testing priority #1 from the brief: an expired or declined direct
    offer must never fall back to nearest-match - that would silently
    override the seller's specific choice."""

    def test_direct_request_creates_a_single_targeted_offer(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        chosen_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        other_rider = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))

        delivery, offer = request_rider_for_seller_order(
            seller_order, seller, mode="direct", rider=chosen_rider
        )

        assert offer is not None
        assert offer.rider_id == chosen_rider.id
        assert delivery.request_mode == Delivery.RequestMode.DIRECT
        assert delivery.initiated_by == Delivery.InitiatedBy.SELLER
        assert delivery.requested_by_id == seller.user_id
        assert not DeliveryOffer.objects.filter(delivery=delivery, rider=other_rider).exists()

    def test_expired_direct_offer_does_not_cascade_to_another_rider(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        chosen_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        # A second rider who WOULD be matched by auto dispatch, to prove the
        # engine deliberately does not fall back to them.
        RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))

        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode="direct", rider=chosen_rider)

        offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])
        expire_offer(offer)

        offer.refresh_from_db()
        delivery.refresh_from_db()
        assert offer.status == DeliveryOffer.Status.EXPIRED
        assert delivery.status == Delivery.Status.PENDING
        assert not DeliveryOffer.objects.filter(
            delivery=delivery, status=DeliveryOffer.Status.PENDING
        ).exists()

    def test_declined_direct_offer_does_not_cascade_to_another_rider(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        chosen_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))

        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode="direct", rider=chosen_rider)

        decline_offer(offer, chosen_rider)

        offer.refresh_from_db()
        delivery.refresh_from_db()
        assert offer.status == DeliveryOffer.Status.DECLINED
        assert delivery.status == Delivery.Status.PENDING
        assert not DeliveryOffer.objects.filter(
            delivery=delivery, status=DeliveryOffer.Status.PENDING
        ).exists()

    def test_auto_request_still_cascades_normally(self):
        """Control case - auto mode must keep cascading exactly like
        Phase 2's system-triggered dispatch, so the no-cascade rule is
        proven to be specific to direct mode, not a global regression."""
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        near_rider = RiderProfileFactory(current_lat=Decimal("5.6099"), current_lng=Decimal("-0.1900"))
        far_rider = RiderProfileFactory(current_lat=Decimal("5.6045"), current_lng=Decimal("-0.1870"))

        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode="auto")
        assert offer.rider_id == near_rider.id

        offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])
        expire_offer(offer)

        cascaded_offer = DeliveryOffer.objects.filter(
            delivery=delivery, status=DeliveryOffer.Status.PENDING
        ).first()
        assert cascaded_offer is not None
        assert cascaded_offer.rider_id == far_rider.id

    @pytest.mark.django_db(transaction=True)
    def test_seller_is_notified_when_a_direct_request_expires(self):
        # transaction=True so the on_commit-deferred notification actually
        # fires - pytest-django's default wrapping never commits, so
        # on_commit callbacks would otherwise silently never run.
        from notifications.models import Notification

        seller = SellerFactory(user=UserFactory(), pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        chosen_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))

        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode="direct", rider=chosen_rider)
        offer.expires_at = timezone.now() - datetime.timedelta(seconds=1)
        offer.save(update_fields=["expires_at"])
        expire_offer(offer)

        assert Notification.objects.filter(user=seller.user, title__icontains="attention").exists()

    @pytest.mark.django_db(transaction=True)
    def test_seller_is_notified_when_the_direct_rider_accepts(self):
        from notifications.models import Notification

        seller = SellerFactory(user=UserFactory(), pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        chosen_rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))

        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode="direct", rider=chosen_rider)
        accept_offer(offer, chosen_rider)

        assert Notification.objects.filter(user=seller.user, title__icontains="on the way").exists()

    def test_direct_dispatch_rejects_an_offline_rider(self):
        seller = SellerFactory(pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"))
        seller_order = _create_seller_order(seller)
        offline_rider = RiderProfileFactory(is_online=False)

        with pytest.raises(RiderRequestError):
            request_rider_for_seller_order(seller_order, seller, mode="direct", rider=offline_rider)

    def test_a_seller_cannot_request_a_rider_for_another_sellers_order(self):
        seller = SellerFactory()
        other_seller = SellerFactory()
        seller_order = _create_seller_order(seller)

        with pytest.raises(RiderRequestError):
            request_rider_for_seller_order(seller_order, other_seller, mode="auto")
