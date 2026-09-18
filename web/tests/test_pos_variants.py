import json

import pytest
from django.test import Client
from django.urls import reverse

from catalog.tests.factories import ProductFactory, ProductVariantFactory
from pos.models import POSSaleItem
from pos.tests.factories import EmployeeFactory

from .test_catalog_management import seller_client


def clocked_in_client(seller):
    employee = EmployeeFactory(seller=seller, pin="9999")
    client = Client()
    client.force_login(seller.user)
    client.post(reverse("web-pos-clock-in"), {"employee_id": employee.id, "pin": "9999"})
    return client, employee


@pytest.mark.django_db
def test_pos_lookup_includes_variants():
    client, seller = seller_client(subscribed=True)
    product = ProductFactory(seller=seller, barcode="12345")
    ProductVariantFactory(product=product, size="M", stock_qty=3)
    clocked_in, _ = clocked_in_client(seller)

    response = clocked_in.get(reverse("web-pos-lookup"), {"code": "12345"})
    data = json.loads(response.content)
    assert data["found"] is True
    assert len(data["product"]["variants"]) == 1
    assert data["product"]["variants"][0]["size"] == "M"


@pytest.mark.django_db
def test_pos_checkout_with_variant_decrements_variant_stock():
    _, seller = seller_client(subscribed=True)
    client, employee = clocked_in_client(seller)
    product = ProductFactory(seller=seller, price="40.00", stock_qty=50)
    variant = ProductVariantFactory(product=product, size="L", stock_qty=4)

    response = client.post(reverse("web-pos-checkout"), {
        "product_id": [str(product.id)],
        "variant_id": [str(variant.id)],
        "qty": ["2"],
        "payment_method": "cash",
        "amount_tendered": "100.00",
    })
    assert response.status_code == 302

    variant.refresh_from_db()
    product.refresh_from_db()
    assert variant.stock_qty == 2
    assert product.stock_qty == 50
    assert POSSaleItem.objects.get().variant_id == variant.id
