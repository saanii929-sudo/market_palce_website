import factory
from factory.django import DjangoModelFactory

from catalog.tests.factories import SellerFactory
from riders.tests.factories import RiderProfileFactory
from sellers.models import SellerFavoriteRider, SellerRiderBlock

__all__ = ["SellerFactory", "SellerFavoriteRiderFactory", "SellerRiderBlockFactory"]


class SellerFavoriteRiderFactory(DjangoModelFactory):
    class Meta:
        model = SellerFavoriteRider

    seller = factory.SubFactory(SellerFactory)
    rider = factory.SubFactory(RiderProfileFactory)


class SellerRiderBlockFactory(DjangoModelFactory):
    class Meta:
        model = SellerRiderBlock

    seller = factory.SubFactory(SellerFactory)
    rider = factory.SubFactory(RiderProfileFactory)
