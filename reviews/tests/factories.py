import factory
from factory.django import DjangoModelFactory

from orders.tests.factories import OrderItemFactory
from reviews.models import Review


class ReviewFactory(DjangoModelFactory):
    class Meta:
        model = Review

    order_item = factory.SubFactory(OrderItemFactory)
    product = factory.LazyAttribute(lambda o: o.order_item.product)
    user = factory.LazyAttribute(lambda o: o.order_item.order.user)
    rating = 5
    comment = "Great!"
