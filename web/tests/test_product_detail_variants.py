import pytest
from django.test import Client
from django.urls import reverse

from catalog.tests.factories import ProductFactory, ProductVariantFactory


def _tag_for(content: str, value: str) -> str:
    marker = f'name="variant_id" value="{value}"'
    start = content.index(marker)
    end = content.index(">", start)
    return content[start:end]


@pytest.mark.django_db
def test_out_of_stock_variant_is_disabled_and_not_default_selected():
    product = ProductFactory()
    sold_out = ProductVariantFactory(product=product, size="S", stock_qty=0)
    available = ProductVariantFactory(product=product, size="M", stock_qty=7)

    response = Client().get(reverse("web-product-detail", args=[product.slug]))
    assert response.status_code == 200
    content = response.content.decode()

    sold_out_tag = _tag_for(content, str(sold_out.id))
    available_tag = _tag_for(content, str(available.id))

    assert "disabled" in sold_out_tag
    assert "checked" not in sold_out_tag
    assert "disabled" not in available_tag
    assert "checked" in available_tag
    assert 'data-stock="7"' in available_tag


@pytest.mark.django_db
def test_all_variants_sold_out_falls_back_to_first_as_selected():
    product = ProductFactory()
    only_variant = ProductVariantFactory(product=product, size="S", stock_qty=0)

    response = Client().get(reverse("web-product-detail", args=[product.slug]))
    assert response.status_code == 200
    content = response.content.decode()
    tag = _tag_for(content, str(only_variant.id))
    assert "checked" in tag
    assert "disabled" in tag
