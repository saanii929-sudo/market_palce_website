import datetime
from decimal import Decimal

import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from accounts.tests.factories import AddressFactory
from deliveries.models import Delivery, DeliveryOffer
from riders.tests.factories import RiderProfileFactory


class DeliveryFactory(DjangoModelFactory):
    class Meta:
        model = Delivery

    # A saved Address stands in for "whatever this delivery is fulfilling" -
    # these tests only care about the dispatch/fare machinery, not what the
    # generic content_object actually is.
    content_object = factory.SubFactory(AddressFactory)
    delivery_type = Delivery.DeliveryType.PARCEL
    pickup_address = "Pickup St"
    pickup_contact_name = "Sender"
    pickup_contact_phone = "0550000000"
    pickup_lat = Decimal("5.610000")
    pickup_lng = Decimal("-0.190000")
    dropoff_address = "Dropoff St"
    dropoff_contact_name = "Recipient"
    dropoff_contact_phone = "0559999999"
    dropoff_lat = Decimal("5.605000")
    dropoff_lng = Decimal("-0.188000")
    distance_km = Decimal("2.50")
    price = Decimal("15.00")


class DeliveryOfferFactory(DjangoModelFactory):
    class Meta:
        model = DeliveryOffer

    delivery = factory.SubFactory(DeliveryFactory)
    rider = factory.SubFactory(RiderProfileFactory)
    sent_at = factory.LazyFunction(timezone.now)
    expires_at = factory.LazyFunction(lambda: timezone.now() + datetime.timedelta(seconds=12))
