from decimal import Decimal
from unittest.mock import Mock, patch

import pytest

from accounts.tests.factories import UserFactory
from deliveries.models import Delivery, DeliveryOffer, Trip
from deliveries.services import accept_offer, complete_trip, confirm_pickup, submit_proof_of_delivery
from parcels.models import Parcel
from parcels.services import (
    ParcelError,
    cancel_parcel,
    check_and_finalize_parcel_payment,
    compute_parcel_price,
    create_parcel,
    find_rider_for_parcel,
    get_delivery_for_parcel,
    get_parcel_quote,
    initiate_parcel_checkout,
)
from riders.tests.factories import RiderProfileFactory


def _mark_paid(parcel):
    parcel.payment_status = Parcel.PaymentStatus.PAID
    parcel.save(update_fields=["payment_status"])
    return parcel


def _create_dispatched_parcel():
    """A parcel with a rider already online and in range, then an explicit
    find-rider call - creation itself no longer auto-dispatches (see
    parcels.services.create_parcel / find_rider_for_parcel). Payment must be
    settled before find-rider will run (see TestParcelPayment)."""
    rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
    sender = UserFactory()
    parcel = create_parcel(
        sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
        pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
        dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
    )
    _mark_paid(parcel)
    find_rider_for_parcel(parcel, sender)
    delivery = get_delivery_for_parcel(parcel)
    offer = DeliveryOffer.objects.get(delivery=delivery)
    return parcel, delivery, offer, rider


def _hubtel_initiate_response(reference="PCL-TEST", checkout_url="https://pay.hubtel.com/checkout/parcel"):
    return Mock(
        status_code=200,
        json=lambda: {
            "responseCode": "0000",
            "data": {"checkoutUrl": checkout_url, "checkoutId": "abc", "clientReference": reference},
        },
        raise_for_status=Mock(),
    )


def _hubtel_status_response(hubtel_status="Paid"):
    return Mock(
        status_code=200,
        json=lambda: {"responseCode": "0000", "data": {"status": hubtel_status}},
        raise_for_status=Mock(),
    )


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

    def test_pickup_address_pin_carries_over_when_no_explicit_coordinates_given(self):
        from accounts.tests.factories import AddressFactory

        sender = UserFactory()
        address = AddressFactory(user=sender, lat=Decimal("5.603700"), lng=Decimal("-0.187000"))

        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_address=address, dropoff_line1="2 B St", dropoff_city="Accra",
        )

        assert parcel.pickup_lat == Decimal("5.603700")
        assert parcel.pickup_lng == Decimal("-0.187000")

    def test_explicit_pickup_coordinates_win_over_the_saved_addresss_pin(self):
        from accounts.tests.factories import AddressFactory

        sender = UserFactory()
        address = AddressFactory(user=sender, lat=Decimal("5.603700"), lng=Decimal("-0.187000"))

        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_address=address, pickup_lat=Decimal("5.700000"), pickup_lng=Decimal("-0.200000"),
            dropoff_line1="2 B St", dropoff_city="Accra",
        )

        assert parcel.pickup_lat == Decimal("5.700000")
        assert parcel.pickup_lng == Decimal("-0.200000")

    def test_pickup_address_with_no_pin_leaves_coordinates_blank(self):
        from accounts.tests.factories import AddressFactory

        sender = UserFactory()
        address = AddressFactory(user=sender)  # no lat/lng set

        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_address=address, dropoff_line1="2 B St", dropoff_city="Accra",
        )

        assert parcel.pickup_lat is None
        assert parcel.pickup_lng is None

    def test_create_parcel_requires_a_dropoff_address(self):
        sender = UserFactory()
        with pytest.raises(ParcelError):
            create_parcel(
                sender, recipient_name="Jane", recipient_phone="0559998888",
                package_size=Parcel.PackageSize.SMALL, pickup_line1="1 A St", pickup_city="Accra",
                dropoff_line1="", dropoff_city="",
            )


@pytest.mark.django_db
class TestFindRiderForParcel:
    def test_create_parcel_does_not_auto_dispatch(self):
        RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        delivery = get_delivery_for_parcel(parcel)
        assert delivery.status == Delivery.Status.PENDING
        assert delivery.dispatch_attempts == 0
        assert not DeliveryOffer.objects.filter(delivery=delivery).exists()

    def test_find_rider_creates_the_offer(self):
        rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        _mark_paid(parcel)

        payload = find_rider_for_parcel(parcel, sender)

        # "rider" only populates once a Trip exists (i.e. after accept) -
        # at this point there's just a pending offer out.
        assert payload["delivery_status"] == "offered"
        assert payload["rider"] is None
        delivery = get_delivery_for_parcel(parcel)
        assert delivery.status == Delivery.Status.OFFERED
        assert DeliveryOffer.objects.get(delivery=delivery).rider_id == rider.id

    def test_find_rider_only_the_sender_can_call_it(self):
        sender = UserFactory()
        other_user = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )
        with pytest.raises(ParcelError):
            find_rider_for_parcel(parcel, other_user)

    def test_find_rider_rejects_once_a_rider_is_assigned(self):
        parcel, delivery, offer, rider = _create_dispatched_parcel()
        accept_offer(offer, rider)
        parcel.refresh_from_db()

        with pytest.raises(ParcelError):
            find_rider_for_parcel(parcel, parcel.sender)

    def test_find_rider_rejects_a_second_search_while_an_offer_is_still_pending(self):
        """Prevents a customer mashing 'find rider' from creating two
        simultaneous offers to two different riders for the same parcel."""
        RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        RiderProfileFactory(current_lat=Decimal("5.6101"), current_lng=Decimal("-0.1900"))
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        _mark_paid(parcel)
        find_rider_for_parcel(parcel, sender)

        with pytest.raises(ParcelError):
            find_rider_for_parcel(parcel, sender)

        delivery = get_delivery_for_parcel(parcel)
        assert DeliveryOffer.objects.filter(delivery=delivery).count() == 1

    def test_find_rider_retry_resets_attempts_after_no_riders_available(self):
        from deliveries.models import MAX_DISPATCH_ATTEMPTS

        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        delivery = get_delivery_for_parcel(parcel)
        delivery.dispatch_attempts = MAX_DISPATCH_ATTEMPTS
        delivery.save(update_fields=["dispatch_attempts"])
        assert delivery.no_riders_available
        _mark_paid(parcel)

        # Now bring a rider online and retry - a stale exhausted counter
        # must not silently block the retry from ever finding them.
        rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        find_rider_for_parcel(parcel, sender)

        delivery.refresh_from_db()
        assert delivery.status == Delivery.Status.OFFERED
        assert DeliveryOffer.objects.get(delivery=delivery).rider_id == rider.id


@pytest.mark.django_db
class TestParcelPayment:
    def test_find_rider_is_blocked_until_payment_is_paid(self):
        RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )
        assert parcel.payment_status == Parcel.PaymentStatus.UNPAID

        with pytest.raises(ParcelError):
            find_rider_for_parcel(parcel, sender)

        delivery = get_delivery_for_parcel(parcel)
        assert not DeliveryOffer.objects.filter(delivery=delivery).exists()

    @patch("requests.post")
    def test_initiate_checkout_sets_the_checkout_url(self, mock_post, settings):
        settings.HUBTEL_API_ID = "id"
        settings.HUBTEL_API_KEY = "key"
        settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
        mock_post.return_value = _hubtel_initiate_response(checkout_url="https://pay.hubtel.com/checkout/parcel-1")

        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )

        parcel = initiate_parcel_checkout(
            parcel, sender,
            callback_url="https://api.example.com/parcels/payment-webhook/",
            return_url="https://app.example.com/return",
            cancellation_url="https://app.example.com/cancel",
        )

        assert parcel.checkout_url == "https://pay.hubtel.com/checkout/parcel-1"
        assert parcel.payment_status == Parcel.PaymentStatus.UNPAID  # still unpaid until confirmed

    def test_only_the_sender_can_initiate_checkout(self):
        sender = UserFactory()
        other_user = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )

        with pytest.raises(ParcelError):
            initiate_parcel_checkout(
                parcel, other_user,
                callback_url="https://api.example.com/parcels/payment-webhook/",
                return_url="https://app.example.com/return",
                cancellation_url="https://app.example.com/cancel",
            )

    @patch("requests.get")
    def test_check_and_finalize_marks_paid_on_success_and_unblocks_find_rider(self, mock_get, settings):
        settings.HUBTEL_API_ID = "id"
        settings.HUBTEL_API_KEY = "key"
        settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
        mock_get.return_value = _hubtel_status_response("Paid")

        rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
        )

        parcel = check_and_finalize_parcel_payment(parcel)
        assert parcel.payment_status == Parcel.PaymentStatus.PAID

        find_rider_for_parcel(parcel, sender)
        delivery = get_delivery_for_parcel(parcel)
        assert DeliveryOffer.objects.get(delivery=delivery).rider_id == rider.id

    @patch("requests.get")
    def test_check_and_finalize_marks_failed_and_keeps_blocking_find_rider(self, mock_get, settings):
        settings.HUBTEL_API_ID = "id"
        settings.HUBTEL_API_KEY = "key"
        settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
        mock_get.return_value = _hubtel_status_response("Unpaid")

        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )

        parcel = check_and_finalize_parcel_payment(parcel)
        assert parcel.payment_status == Parcel.PaymentStatus.FAILED

        with pytest.raises(ParcelError):
            find_rider_for_parcel(parcel, sender)

    @patch("requests.get")
    def test_check_and_finalize_is_a_no_op_once_already_resolved(self, mock_get, settings):
        settings.HUBTEL_API_ID = "id"
        settings.HUBTEL_API_KEY = "key"
        settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
        mock_get.return_value = _hubtel_status_response("Paid")

        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )
        _mark_paid(parcel)

        check_and_finalize_parcel_payment(parcel)

        mock_get.assert_not_called()


@pytest.mark.django_db
class TestCashOnPickupParcels:
    def _create_cash_parcel(self, sender=None):
        rider = RiderProfileFactory(current_lat=Decimal("5.6100"), current_lng=Decimal("-0.1900"))
        sender = sender or UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", pickup_lat=Decimal("5.6100"), pickup_lng=Decimal("-0.1900"),
            dropoff_line1="2 B St", dropoff_city="Accra", dropoff_lat=Decimal("5.6050"), dropoff_lng=Decimal("-0.1880"),
            payment_method=Parcel.PaymentMethod.CASH,
        )
        return parcel, sender, rider

    def test_defaults_to_online_when_not_specified(self):
        sender = UserFactory()
        parcel = create_parcel(
            sender, recipient_name="Jane", recipient_phone="0559998888", package_size=Parcel.PackageSize.SMALL,
            pickup_line1="1 A St", pickup_city="Accra", dropoff_line1="2 B St", dropoff_city="Accra",
        )
        assert parcel.payment_method == Parcel.PaymentMethod.ONLINE

    def test_cash_parcel_can_find_a_rider_while_still_unpaid(self):
        parcel, sender, rider = self._create_cash_parcel()
        assert parcel.payment_status == Parcel.PaymentStatus.UNPAID

        find_rider_for_parcel(parcel, sender)

        delivery = get_delivery_for_parcel(parcel)
        assert DeliveryOffer.objects.get(delivery=delivery).rider_id == rider.id
        parcel.refresh_from_db()
        assert parcel.payment_status == Parcel.PaymentStatus.UNPAID  # not paid yet - collected at pickup

    def test_pickup_confirmation_marks_the_cash_parcel_paid(self):
        parcel, sender, rider = self._create_cash_parcel()
        find_rider_for_parcel(parcel, sender)
        delivery = get_delivery_for_parcel(parcel)
        offer = DeliveryOffer.objects.get(delivery=delivery)
        trip = accept_offer(offer, rider)

        confirm_pickup(trip, rider)

        parcel.refresh_from_db()
        assert parcel.payment_status == Parcel.PaymentStatus.PAID

    def test_checkout_is_rejected_for_a_cash_parcel(self):
        parcel, sender, rider = self._create_cash_parcel()
        with pytest.raises(ParcelError):
            initiate_parcel_checkout(
                parcel, sender,
                callback_url="https://api.example.com/parcels/payment-webhook/",
                return_url="https://app.example.com/return",
                cancellation_url="https://app.example.com/cancel",
            )

    def test_cash_trip_completion_nets_out_the_platform_commission_from_rider_earnings(self):
        from riders.models import RiderEarning

        parcel, sender, rider = self._create_cash_parcel()
        find_rider_for_parcel(parcel, sender)
        delivery = get_delivery_for_parcel(parcel)
        offer = DeliveryOffer.objects.get(delivery=delivery)
        trip = accept_offer(offer, rider)
        confirm_pickup(trip, rider)
        pod = trip.proof_of_delivery
        submit_proof_of_delivery(trip, rider, otp_code=pod.otp_code)
        complete_trip(trip, rider)

        delivery.refresh_from_db()
        earning = RiderEarning.objects.get(trip=trip)
        expected_net = delivery.rider_fare - delivery.price
        assert expected_net < 0  # the rider already pocketed more in cash than their fare share
        assert earning.total == expected_net

    def test_online_trip_completion_still_credits_the_full_fare(self):
        from riders.models import RiderEarning

        parcel, delivery, offer, rider = _create_dispatched_parcel()  # payment_method defaults to ONLINE
        trip = accept_offer(offer, rider)
        confirm_pickup(trip, rider)
        pod = trip.proof_of_delivery
        submit_proof_of_delivery(trip, rider, otp_code=pod.otp_code)
        complete_trip(trip, rider)

        delivery.refresh_from_db()
        earning = RiderEarning.objects.get(trip=trip)
        assert earning.total == delivery.rider_fare


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
