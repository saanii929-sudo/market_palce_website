import factory
from factory.django import DjangoModelFactory

from accounts.tests.factories import UserFactory
from payments.models import PaymentMethodToken


class PaymentMethodTokenFactory(DjangoModelFactory):
    class Meta:
        model = PaymentMethodToken

    user = factory.SubFactory(UserFactory)
    gateway = PaymentMethodToken.Gateway.PAYSTACK
    token = factory.Sequence(lambda n: f"tok_{n}")
    brand = "visa"
    last4 = "4242"
