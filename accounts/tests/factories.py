import factory
from factory.django import DjangoModelFactory

from accounts.models import Address, OTPCode, User


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("email",)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    full_name = factory.Faker("name")
    is_email_verified = True
    password = factory.PostGenerationMethodCall("set_password", "StrongPass123!")


class OTPCodeFactory(DjangoModelFactory):
    class Meta:
        model = OTPCode

    destination = factory.Sequence(lambda n: f"user{n}@example.com")
    channel = OTPCode.Channel.EMAIL
    purpose = OTPCode.Purpose.SIGNUP_VERIFY


class AddressFactory(DjangoModelFactory):
    class Meta:
        model = Address

    user = factory.SubFactory(UserFactory)
    recipient_name = "Jane Doe"
    phone = "+233201234567"
    line1 = "123 Independence Ave"
    city = "Accra"
    country = "Ghana"
