import factory
from factory.django import DjangoModelFactory

from catalog.tests.factories import SellerFactory
from pos.models import Employee


class EmployeeFactory(DjangoModelFactory):
    class Meta:
        model = Employee

    seller = factory.SubFactory(SellerFactory)
    full_name = factory.Sequence(lambda n: f"Employee {n}")
    role = Employee.Role.CASHIER
    is_active = True

    @factory.post_generation
    def pin(self, create, extracted, **kwargs):
        if not create:
            return
        self.set_pin(extracted or "1234")
        self.save(update_fields=["pin_hash"])
