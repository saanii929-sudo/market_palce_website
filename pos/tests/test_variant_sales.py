import pytest

from catalog.tests.factories import ProductFactory, ProductVariantFactory

from ..models import POSSaleItem
from ..services import POSError, complete_sale, process_return
from .factories import EmployeeFactory


@pytest.mark.django_db
def test_sale_without_variant_decrements_product_stock():
    employee = EmployeeFactory()
    product = ProductFactory(seller=employee.seller, price="20.00", stock_qty=10)

    sale = complete_sale(
        seller=employee.seller, employee=employee,
        cart_lines=[{"product_id": product.id, "variant_id": None, "qty": 3}],
        payment_method="cash",
    )

    product.refresh_from_db()
    assert product.stock_qty == 7
    assert product.sold_count == 3
    item = sale.items.get()
    assert item.variant is None


@pytest.mark.django_db
def test_sale_with_variant_decrements_variant_stock_not_product_stock():
    employee = EmployeeFactory()
    product = ProductFactory(seller=employee.seller, price="30.00", stock_qty=100)
    variant = ProductVariantFactory(product=product, size="M", stock_qty=5)

    sale = complete_sale(
        seller=employee.seller, employee=employee,
        cart_lines=[{"product_id": product.id, "variant_id": variant.id, "qty": 2}],
        payment_method="cash",
    )

    product.refresh_from_db()
    variant.refresh_from_db()
    assert product.stock_qty == 100  # untouched
    assert product.sold_count == 2  # still tracked at the product level
    assert variant.stock_qty == 3

    item = sale.items.get()
    assert item.variant_id == variant.id


@pytest.mark.django_db
def test_sale_rejects_insufficient_variant_stock_and_rolls_back():
    employee = EmployeeFactory()
    product = ProductFactory(seller=employee.seller, price="30.00", stock_qty=100)
    variant = ProductVariantFactory(product=product, size="S", stock_qty=1)

    with pytest.raises(POSError):
        complete_sale(
            seller=employee.seller, employee=employee,
            cart_lines=[{"product_id": product.id, "variant_id": variant.id, "qty": 2}],
            payment_method="cash",
        )

    variant.refresh_from_db()
    product.refresh_from_db()
    assert variant.stock_qty == 1
    assert product.sold_count == 0
    assert not POSSaleItem.objects.exists()


@pytest.mark.django_db
def test_two_variants_of_same_product_are_independent_lines():
    employee = EmployeeFactory()
    product = ProductFactory(seller=employee.seller, price="30.00", stock_qty=100)
    small = ProductVariantFactory(product=product, size="S", stock_qty=2)
    large = ProductVariantFactory(product=product, size="L", stock_qty=10)

    sale = complete_sale(
        seller=employee.seller, employee=employee,
        cart_lines=[
            {"product_id": product.id, "variant_id": small.id, "qty": 2},
            {"product_id": product.id, "variant_id": large.id, "qty": 4},
        ],
        payment_method="cash",
    )

    small.refresh_from_db()
    large.refresh_from_db()
    assert small.stock_qty == 0
    assert large.stock_qty == 6
    assert sale.items.count() == 2
    assert sale.subtotal == pytest.approx(180.0)  # (2 + 4) * 30.00


@pytest.mark.django_db
def test_returning_a_variant_item_restocks_the_variant():
    employee = EmployeeFactory()
    product = ProductFactory(seller=employee.seller, price="25.00", stock_qty=100)
    variant = ProductVariantFactory(product=product, size="M", stock_qty=5)

    sale = complete_sale(
        seller=employee.seller, employee=employee,
        cart_lines=[{"product_id": product.id, "variant_id": variant.id, "qty": 3}],
        payment_method="cash",
    )
    variant.refresh_from_db()
    assert variant.stock_qty == 2

    sale_item = sale.items.get()
    process_return(sale=sale, lines=[{"sale_item_id": sale_item.id, "qty": 2}], restock=True)

    variant.refresh_from_db()
    product.refresh_from_db()
    assert variant.stock_qty == 4  # 2 + 2 returned
    assert product.stock_qty == 100  # never touched for a variant sale
