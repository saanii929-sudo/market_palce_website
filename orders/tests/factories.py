import factory
from factory.django import DjangoModelFactory

from accounts.tests.factories import AddressFactory, UserFactory
from catalog.tests.factories import ProductFactory
from orders.models import DeliveryMethod, Order, OrderItem, PaymentMethod


class DeliveryMethodFactory(DjangoModelFactory):
    class Meta:
        model = DeliveryMethod
        django_get_or_create = ("code",)

    name = "Standard"
    code = "standard"
    price = "5.00"
    eta_days_min = 2
    eta_days_max = 5


class PaymentMethodFactory(DjangoModelFactory):
    class Meta:
        model = PaymentMethod
        django_get_or_create = ("code",)

    name = "Cash on Delivery"
    code = "cash_on_delivery"


class OrderFactory(DjangoModelFactory):
    class Meta:
        model = Order

    user = factory.SubFactory(UserFactory)
    status = Order.Status.DELIVERED
    subtotal = "50.00"
    total = "50.00"
    delivery_method = factory.SubFactory(DeliveryMethodFactory)
    payment_method = factory.SubFactory(PaymentMethodFactory)

    @factory.post_generation
    def address(self, create, extracted, **kwargs):
        if not create:
            return
        address = extracted or AddressFactory(user=self.user)
        self.snapshot_address(address)
        self.save()


class OrderItemFactory(DjangoModelFactory):
    class Meta:
        model = OrderItem

    order = factory.SubFactory(OrderFactory)
    product = factory.SubFactory(ProductFactory)
    qty = 1
    unit_price = "50.00"
