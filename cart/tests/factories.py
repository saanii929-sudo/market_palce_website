import factory
from factory.django import DjangoModelFactory

from cart.models import Cart, Coupon


class CouponFactory(DjangoModelFactory):
    class Meta:
        model = Coupon
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"COUPON{n}")
    discount_type = Coupon.DiscountType.PERCENTAGE
    value = "10.00"


class CartFactory(DjangoModelFactory):
    class Meta:
        model = Cart
