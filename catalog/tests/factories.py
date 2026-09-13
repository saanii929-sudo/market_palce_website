import factory
from django.utils import timezone
from factory.django import DjangoModelFactory

from catalog.models import (
    Banner,
    Brand,
    Category,
    Collection,
    FlashDeal,
    Product,
    ProductImage,
    ProductVariant,
    Seller,
    Subcategory,
)


class CategoryFactory(DjangoModelFactory):
    class Meta:
        model = Category
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Category {n}")
    slug = factory.Sequence(lambda n: f"category-{n}")


class SubcategoryFactory(DjangoModelFactory):
    class Meta:
        model = Subcategory
        django_get_or_create = ("slug",)

    category = factory.SubFactory(CategoryFactory)
    name = factory.Sequence(lambda n: f"Subcategory {n}")
    slug = factory.Sequence(lambda n: f"subcategory-{n}")


class BrandFactory(DjangoModelFactory):
    class Meta:
        model = Brand
        django_get_or_create = ("slug",)

    name = factory.Sequence(lambda n: f"Brand {n}")
    slug = factory.Sequence(lambda n: f"brand-{n}")


class SellerFactory(DjangoModelFactory):
    class Meta:
        model = Seller
        django_get_or_create = ("slug",)

    business_name = factory.Sequence(lambda n: f"Seller {n}")
    slug = factory.Sequence(lambda n: f"seller-{n}")


class ProductFactory(DjangoModelFactory):
    class Meta:
        model = Product
        django_get_or_create = ("slug",)

    seller = factory.SubFactory(SellerFactory)
    category = factory.SubFactory(CategoryFactory)
    name = factory.Sequence(lambda n: f"Product {n}")
    slug = factory.Sequence(lambda n: f"product-{n}")
    description = "A great product."
    price = "49.99"
    sku = factory.Sequence(lambda n: f"SKU{n:06d}")
    stock_qty = 10
    is_active = True


class ProductImageFactory(DjangoModelFactory):
    class Meta:
        model = ProductImage

    product = factory.SubFactory(ProductFactory)
    display_order = 0


class ProductVariantFactory(DjangoModelFactory):
    class Meta:
        model = ProductVariant

    product = factory.SubFactory(ProductFactory)
    size = "M"
    color = "Black"
    stock_qty = 5


class FlashDealFactory(DjangoModelFactory):
    class Meta:
        model = FlashDeal

    product = factory.SubFactory(ProductFactory)
    deal_price = "29.99"
    stock_qty = 10
    stock_sold = 0
    starts_at = factory.LazyFunction(lambda: timezone.now() - timezone.timedelta(hours=1))
    ends_at = factory.LazyFunction(lambda: timezone.now() + timezone.timedelta(hours=1))


class CollectionFactory(DjangoModelFactory):
    class Meta:
        model = Collection
        django_get_or_create = ("slug",)

    title = factory.Sequence(lambda n: f"Collection {n}")
    slug = factory.Sequence(lambda n: f"collection-{n}")


class BannerFactory(DjangoModelFactory):
    class Meta:
        model = Banner

    title = factory.Sequence(lambda n: f"Banner {n}")
