import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from .factories import DeliveryMethodFactory


@pytest.mark.django_db
def test_delivery_method_list_is_public_and_returns_active_only():
    DeliveryMethodFactory(name="Standard", code="standard", price="5.00")
    DeliveryMethodFactory(name="Express", code="express", price="15.00")
    DeliveryMethodFactory(name="Retired", code="retired", price="1.00", is_active=False)

    response = APIClient().get(reverse("delivery-method-list"))

    assert response.status_code == 200
    codes = [item["code"] for item in response.data]
    assert codes == ["standard", "express"]
    assert "retired" not in codes


@pytest.mark.django_db
def test_delivery_method_list_is_ordered_by_price():
    DeliveryMethodFactory(name="Express", code="express", price="15.00")
    DeliveryMethodFactory(name="Standard", code="standard", price="5.00")

    response = APIClient().get(reverse("delivery-method-list"))

    prices = [float(item["price"]) for item in response.data]
    assert prices == sorted(prices)
