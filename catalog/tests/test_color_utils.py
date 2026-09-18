import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from ..color_utils import resolve_css_color
from .factories import ProductFactory, ProductVariantFactory


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("Black", "black"),
        ("Sky Blue", "skyblue"),
        ("Dark-Green", "darkgreen"),
        ("Charcoal", "#36454f"),
        ("BURGUNDY", "#800020"),
    ],
)
def test_resolve_css_color(raw, expected):
    assert resolve_css_color(raw) == expected


@pytest.mark.django_db
def test_product_detail_exposes_variant_color_swatch():
    product = ProductFactory()
    ProductVariantFactory(product=product, size="M", color="Charcoal")

    response = APIClient().get(reverse("product-detail", kwargs={"slug": product.slug}))
    variant = response.data["variants"][0]
    assert variant["color"] == "Charcoal"
    assert variant["color_swatch"] == "#36454f"
