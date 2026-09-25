from decimal import Decimal

import factory
from factory.django import DjangoModelFactory

from accounts.tests.factories import UserFactory
from riders.models import RiderProfile, Vehicle


class RiderProfileFactory(DjangoModelFactory):
    class Meta:
        model = RiderProfile

    user = factory.SubFactory(UserFactory)
    is_online = True
    is_verified = True
    current_lat = Decimal("5.603700")
    current_lng = Decimal("-0.187000")


class VehicleFactory(DjangoModelFactory):
    class Meta:
        model = Vehicle

    rider = factory.SubFactory(RiderProfileFactory)
    type = Vehicle.Type.MOTORCYCLE
    plate_number = factory.Sequence(lambda n: f"GX-{n:03d}-24")
