import pytest
from django.urls import reverse

from catalog.models import Product
from catalog.tests.factories import ProductFactory
from pos.models import WarehouseStock
from pos.services import POSError, import_from_warehouse, receive_into_warehouse

from .test_catalog_management import seller_client


@pytest.mark.django_db
def test_seller_can_turn_on_warehouse_from_store_settings():
    client, seller = seller_client()
    assert seller.has_warehouse is False

    response = client.post(reverse("web-seller-settings"), {
        "business_name": seller.business_name, "support_phone": "", "tagline": "", "has_warehouse": "1",
    })
    assert response.status_code == 302

    seller.refresh_from_db()
    assert seller.has_warehouse is True


@pytest.mark.django_db
def test_unchecking_the_box_turns_warehouse_back_off():
    client, seller = seller_client()
    seller.has_warehouse = True
    seller.save(update_fields=["has_warehouse"])

    client.post(reverse("web-seller-settings"), {
        "business_name": seller.business_name, "support_phone": "", "tagline": "",
    })

    seller.refresh_from_db()
    assert seller.has_warehouse is False


@pytest.mark.django_db
class TestWarehouseServiceFunctions:
    def test_receiving_stock_requires_warehouse_to_be_enabled(self):
        product = ProductFactory(stock_qty=0)
        with pytest.raises(POSError):
            receive_into_warehouse(product, qty=10)

    def test_receiving_stock_adds_to_the_warehouse_balance_without_touching_storefront_stock(self):
        product = ProductFactory(stock_qty=0)
        product.seller.has_warehouse = True
        product.seller.save(update_fields=["has_warehouse"])

        receive_into_warehouse(product, qty=10)
        receive_into_warehouse(product, qty=5)

        product.refresh_from_db()
        assert product.stock_qty == 0
        assert WarehouseStock.objects.get(product=product).qty_on_hand == 15

    def test_receiving_a_non_positive_quantity_is_rejected(self):
        product = ProductFactory()
        product.seller.has_warehouse = True
        product.seller.save(update_fields=["has_warehouse"])

        with pytest.raises(POSError):
            receive_into_warehouse(product, qty=0)

    def test_import_moves_units_from_warehouse_onto_the_storefront(self):
        product = ProductFactory(stock_qty=0)
        product.seller.has_warehouse = True
        product.seller.save(update_fields=["has_warehouse"])
        receive_into_warehouse(product, qty=10)

        updated = import_from_warehouse(product, qty=4, user=None)

        assert updated.stock_qty == 4
        assert WarehouseStock.objects.get(product=product).qty_on_hand == 6

    def test_import_fails_without_enough_warehouse_stock(self):
        product = ProductFactory(stock_qty=0)
        product.seller.has_warehouse = True
        product.seller.save(update_fields=["has_warehouse"])
        receive_into_warehouse(product, qty=2)

        with pytest.raises(POSError):
            import_from_warehouse(product, qty=5, user=None)

        product.refresh_from_db()
        assert product.stock_qty == 0

    def test_import_fails_when_nothing_has_ever_been_received(self):
        product = ProductFactory(stock_qty=0)
        product.seller.has_warehouse = True
        product.seller.save(update_fields=["has_warehouse"])

        with pytest.raises(POSError):
            import_from_warehouse(product, qty=1, user=None)

    def test_import_requires_warehouse_to_be_enabled(self):
        product = ProductFactory(stock_qty=0)
        with pytest.raises(POSError):
            import_from_warehouse(product, qty=1, user=None)


@pytest.mark.django_db
class TestWarehousePages:
    def test_warehouse_page_prompts_to_enable_it_when_off(self):
        client, seller = seller_client(subscribed=True)
        response = client.get(reverse("web-seller-warehouse"))
        assert response.status_code == 200
        assert b"Warehouse tracking is off" in response.content

    def test_warehouse_page_renders_products_with_no_warehouse_stock_recorded_yet(self):
        """The common case: warehouse tracking is on, but this particular
        product has never had any stock received into the warehouse, so it
        has no WarehouseStock row at all yet - the page must show 0, not
        crash on the missing reverse relation."""
        client, seller = seller_client(subscribed=True)
        seller.has_warehouse = True
        seller.save(update_fields=["has_warehouse"])
        ProductFactory(seller=seller, stock_qty=0, name="Never Restocked")

        response = client.get(reverse("web-seller-warehouse"))

        assert response.status_code == 200
        assert b"Never Restocked" in response.content

    def test_seller_can_receive_stock_into_the_warehouse_from_the_page(self):
        client, seller = seller_client(subscribed=True)
        seller.has_warehouse = True
        seller.save(update_fields=["has_warehouse"])
        product = ProductFactory(seller=seller, stock_qty=0)

        response = client.post(reverse("web-seller-warehouse-receive", args=[product.id]), {"qty": "20"})
        assert response.status_code == 302

        assert WarehouseStock.objects.get(product=product).qty_on_hand == 20
        product.refresh_from_db()
        assert product.stock_qty == 0

    def test_seller_can_import_from_warehouse_on_the_products_page(self):
        client, seller = seller_client(subscribed=True)
        seller.has_warehouse = True
        seller.save(update_fields=["has_warehouse"])
        product = ProductFactory(seller=seller, stock_qty=0)
        receive_into_warehouse(product, qty=8)

        response = client.get(reverse("web-seller-products"))
        assert b"Import (8 in warehouse)" in response.content

        response = client.post(reverse("web-seller-product-warehouse-import", args=[product.id]), {"qty": "3"})
        assert response.status_code == 302

        product.refresh_from_db()
        assert product.stock_qty == 3
        assert WarehouseStock.objects.get(product=product).qty_on_hand == 5

    def test_products_page_does_not_offer_import_when_warehouse_is_off(self):
        client, seller = seller_client(subscribed=True)
        product = ProductFactory(seller=seller, stock_qty=0)

        response = client.get(reverse("web-seller-products"))
        assert b"Import (" not in response.content

    def test_import_over_the_available_warehouse_balance_leaves_stock_unchanged(self):
        client, seller = seller_client(subscribed=True)
        seller.has_warehouse = True
        seller.save(update_fields=["has_warehouse"])
        product = ProductFactory(seller=seller, stock_qty=0)
        receive_into_warehouse(product, qty=2)

        response = client.post(reverse("web-seller-product-warehouse-import", args=[product.id]), {"qty": "50"})
        assert response.status_code == 302

        product.refresh_from_db()
        assert product.stock_qty == 0
        assert WarehouseStock.objects.get(product=product).qty_on_hand == 2
