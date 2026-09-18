import pytest
from django.test import Client
from django.urls import reverse

from accounts.tests.factories import UserFactory
from catalog.models import Banner, Brand, Collection, FlashDeal, Product, ProductVariant, Subcategory
from catalog.tests.factories import CategoryFactory, ProductFactory, SellerFactory


def admin_client():
    admin = UserFactory(is_superuser=True, is_staff=True)
    client = Client()
    client.force_login(admin)
    return client, admin


def seller_client(subscribed=False):
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)
    if subscribed:
        from django.utils import timezone

        from sellers.models import SellerSubscription, SubscriptionPlan

        plan = SubscriptionPlan.objects.create(name="Growth", slug=f"growth-{seller.id}", price="120.00")
        SellerSubscription.objects.create(
            seller=seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.ACTIVE,
            starts_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(days=30),
        )
    client = Client()
    client.force_login(seller_user)
    return client, seller


@pytest.mark.django_db
def test_admin_can_add_brand():
    client, _ = admin_client()
    response = client.post(reverse("web-console-brand-add"), {"name": "Nike", "logo_url": "", "is_active": "1"})
    assert response.status_code == 302
    assert Brand.objects.filter(name="Nike").exists()


@pytest.mark.django_db
def test_admin_can_add_subcategory_under_a_category():
    client, _ = admin_client()
    category = CategoryFactory(name="Football")
    response = client.post(reverse("web-console-subcategory-add", args=[category.id]), {"name": "Boots"})
    assert response.status_code == 302
    assert Subcategory.objects.filter(category=category, name="Boots").exists()


@pytest.mark.django_db
def test_admin_can_add_banner():
    client, _ = admin_client()
    response = client.post(reverse("web-console-banner-add"), {
        "title": "Big Sale", "subtitle": "", "image_url": "https://example.com/x.jpg",
        "cta_label": "", "cta_link": "", "active_from": "", "active_to": "", "display_order": "0", "is_active": "1",
    })
    assert response.status_code == 302
    assert Banner.objects.filter(title="Big Sale").exists()


@pytest.mark.django_db
def test_admin_can_add_collection_with_products():
    client, _ = admin_client()
    product = ProductFactory(slug="test-shoe")
    response = client.post(reverse("web-console-collection-add"), {
        "title": "Best Sellers", "banner_image_url": "", "linked_category_id": "",
        "display_order": "0", "is_active": "1", "product_slugs": "test-shoe",
    })
    assert response.status_code == 302
    collection = Collection.objects.get(title="Best Sellers")
    assert product in collection.products.all()


@pytest.mark.django_db
def test_non_admin_cannot_access_console_catalog_pages():
    user = UserFactory()
    client = Client()
    client.force_login(user)
    response = client.get(reverse("web-console-brands"))
    assert response.status_code == 302  # redirected away, not a superadmin


@pytest.mark.django_db
def test_adding_a_product_lands_on_its_edit_page_where_variants_can_be_added():
    client, seller = seller_client()
    category = CategoryFactory()
    response = client.post(reverse("web-seller-product-add"), {
        "name": "Running Shoes", "category_id": category.id, "price": "150.00", "stock_qty": "10",
    })
    product = Product.objects.get(name="Running Shoes", seller=seller)
    assert response.status_code == 302
    assert response.url == reverse("web-seller-product-edit", args=[product.id])

    edit_page = client.get(response.url)
    assert b"Variants" in edit_page.content


@pytest.mark.django_db
def test_seller_can_add_variant_to_own_product():
    client, seller = seller_client()
    product = ProductFactory(seller=seller)
    response = client.post(
        reverse("web-seller-product-variant-add", args=[product.id]),
        {"size": "M", "color": "Black", "stock_qty": "5"},
    )
    assert response.status_code == 302
    assert ProductVariant.objects.filter(product=product, size="M", color="Black", stock_qty=5).exists()


@pytest.mark.django_db
def test_seller_cannot_add_variant_to_someone_elses_product():
    client, _ = seller_client()
    other_seller = SellerFactory()
    other_product = ProductFactory(seller=other_seller)
    response = client.post(
        reverse("web-seller-product-variant-add", args=[other_product.id]),
        {"size": "M", "color": "Black", "stock_qty": "5"},
    )
    assert response.status_code == 404
    assert not ProductVariant.objects.filter(product=other_product).exists()


@pytest.mark.django_db
def test_seller_can_add_flash_deal_for_own_product():
    client, seller = seller_client()
    product = ProductFactory(seller=seller, price="100.00")
    response = client.post(reverse("web-seller-flash-deal-add"), {
        "product_id": product.id, "deal_price": "70.00", "stock_qty": "10",
        "starts_at": "2026-01-01T00:00", "ends_at": "2026-01-02T00:00", "is_active": "1",
    })
    assert response.status_code == 302
    assert FlashDeal.objects.filter(product=product, deal_price="70.00").exists()


@pytest.mark.django_db
def test_flash_deal_price_must_be_below_regular_price():
    client, seller = seller_client()
    product = ProductFactory(seller=seller, price="100.00")
    response = client.post(reverse("web-seller-flash-deal-add"), {
        "product_id": product.id, "deal_price": "150.00", "stock_qty": "10",
        "starts_at": "2026-01-01T00:00", "ends_at": "2026-01-02T00:00", "is_active": "1",
    })
    assert response.status_code == 200  # re-renders the form with an error
    assert not FlashDeal.objects.filter(product=product).exists()


@pytest.mark.django_db
def test_seller_cannot_create_flash_deal_for_other_sellers_product():
    client, _ = seller_client()
    other_seller = SellerFactory()
    other_product = ProductFactory(seller=other_seller, price="100.00")
    response = client.post(reverse("web-seller-flash-deal-add"), {
        "product_id": other_product.id, "deal_price": "70.00", "stock_qty": "10",
        "starts_at": "2026-01-01T00:00", "ends_at": "2026-01-02T00:00", "is_active": "1",
    })
    assert response.status_code == 200
    assert not FlashDeal.objects.filter(product=other_product).exists()
