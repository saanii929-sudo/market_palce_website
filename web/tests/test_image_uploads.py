import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from PIL import Image

from catalog.models import Banner, Brand, Category, Collection, ProductImage
from catalog.tests.factories import CategoryFactory, ProductFactory, SellerFactory

from .test_catalog_management import admin_client, seller_client


def fake_image(name="photo.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color="red").save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile(name, buffer.read(), content_type="image/png")


@pytest.mark.django_db
def test_admin_can_upload_category_icon_file():
    client, _ = admin_client()
    response = client.post(
        reverse("web-console-category-add"),
        {"name": "Cycling", "display_order": "0", "commission_rate": "10.00", "is_active": "1", "icon": fake_image()},
        format="multipart",
    )
    assert response.status_code == 302
    category = Category.objects.get(name="Cycling")
    assert category.icon


@pytest.mark.django_db
def test_admin_can_upload_brand_logo_file():
    client, _ = admin_client()
    response = client.post(
        reverse("web-console-brand-add"), {"name": "Puma", "is_active": "1", "logo": fake_image()}, format="multipart"
    )
    assert response.status_code == 302
    assert Brand.objects.get(name="Puma").logo


@pytest.mark.django_db
def test_admin_can_upload_banner_image_file():
    client, _ = admin_client()
    response = client.post(reverse("web-console-banner-add"), {
        "title": "Season Sale", "subtitle": "", "cta_label": "", "cta_link": "",
        "active_from": "", "active_to": "", "display_order": "0", "is_active": "1", "image": fake_image(),
    }, format="multipart")
    assert response.status_code == 302
    assert Banner.objects.get(title="Season Sale").image


@pytest.mark.django_db
def test_admin_can_upload_collection_banner_image_file():
    client, _ = admin_client()
    response = client.post(reverse("web-console-collection-add"), {
        "title": "Winter Picks", "linked_category_id": "", "display_order": "0",
        "is_active": "1", "product_slugs": "", "banner_image": fake_image(),
    }, format="multipart")
    assert response.status_code == 302
    assert Collection.objects.get(title="Winter Picks").banner_image


@pytest.mark.django_db
def test_editing_without_a_new_file_keeps_existing_image():
    client, _ = admin_client()
    category = CategoryFactory(icon=fake_image())
    original_name = category.icon.name

    client.post(reverse("web-console-category-edit", args=[category.id]), {
        "name": category.name, "display_order": "0", "commission_rate": "10.00", "is_active": "1",
    })
    category.refresh_from_db()
    assert category.icon.name == original_name


@pytest.mark.django_db
def test_seller_can_add_multiple_product_photos_and_remove_one():
    client, seller = seller_client()
    product = ProductFactory(seller=seller)

    response = client.post(
        reverse("web-seller-product-edit", args=[product.id]),
        {
            "name": product.name, "category_id": product.category_id, "price": str(product.price),
            "stock_qty": "5", "images": [fake_image("a.png"), fake_image("b.png")],
        },
        format="multipart",
    )
    assert response.status_code == 302
    assert ProductImage.objects.filter(product=product).count() == 2

    image = ProductImage.objects.filter(product=product).first()
    client.post(reverse("web-seller-product-image-delete", args=[product.id, image.id]))
    assert ProductImage.objects.filter(product=product).count() == 1


@pytest.mark.django_db
def test_seller_cannot_delete_another_sellers_product_image():
    client, _ = seller_client()
    other_seller = SellerFactory()
    other_product = ProductFactory(seller=other_seller)
    image = ProductImage.objects.create(product=other_product, image=fake_image())

    response = client.post(reverse("web-seller-product-image-delete", args=[other_product.id, image.id]))
    assert response.status_code == 404
    assert ProductImage.objects.filter(id=image.id).exists()


@pytest.mark.django_db
def test_seller_can_upload_store_logo():
    client, seller = seller_client()
    response = client.post(reverse("web-seller-settings"), {
        "business_name": seller.business_name, "primary_category_id": "", "support_phone": "",
        "tagline": "", "logo": fake_image(),
    }, format="multipart")
    assert response.status_code == 302
    seller.refresh_from_db()
    assert seller.logo
