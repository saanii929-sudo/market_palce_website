from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .color_utils import resolve_css_color
from .models import (
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


class SubcategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Subcategory
        fields = ["id", "name", "slug", "display_order"]


class CategorySerializer(serializers.ModelSerializer):
    subcategories = SubcategorySerializer(many=True, read_only=True)
    icon = serializers.CharField(source="resolved_icon_url", read_only=True, allow_null=True)

    class Meta:
        model = Category
        fields = ["id", "name", "slug", "icon", "display_order", "is_active", "subcategories"]


class BrandSerializer(serializers.ModelSerializer):
    logo = serializers.CharField(source="resolved_logo_url", read_only=True, allow_null=True)

    class Meta:
        model = Brand
        fields = ["id", "name", "slug", "logo"]


class SellerSerializer(serializers.ModelSerializer):
    logo = serializers.CharField(source="resolved_logo_url", read_only=True, allow_null=True)

    class Meta:
        model = Seller
        fields = ["id", "business_name", "slug", "tagline", "logo", "rating", "is_verified", "is_featured"]


class ProductImageSerializer(serializers.ModelSerializer):
    url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = ["id", "url", "display_order"]

    def get_url(self, obj) -> str | None:
        url = obj.resolved_url
        if not url:
            return None
        if url.startswith("http"):
            return url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ProductVariantSerializer(serializers.ModelSerializer):
    color_swatch = serializers.SerializerMethodField()

    class Meta:
        model = ProductVariant
        fields = ["id", "size", "color", "color_swatch", "stock_qty", "in_stock"]

    @extend_schema_field(OpenApiTypes.STR)
    def get_color_swatch(self, variant):
        return resolve_css_color(variant.color)


class ProductListSerializer(serializers.ModelSerializer):
    seller = SellerSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    brand = BrandSerializer(read_only=True)
    discount_percent = serializers.ReadOnlyField()
    primary_image = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "price",
            "original_price",
            "discount_percent",
            "avg_rating",
            "review_count",
            "sold_count",
            "is_featured",
            "seller",
            "category",
            "brand",
            "primary_image",
        ]

    def get_primary_image(self, obj) -> str | None:
        image = obj.images.order_by("display_order", "id").first()
        if not image:
            return None
        url = image.resolved_url
        if not url:
            return None
        if url.startswith("http"):
            return url
        request = self.context.get("request")
        return request.build_absolute_uri(url) if request else url


class ProductDetailSerializer(serializers.ModelSerializer):
    seller = SellerSerializer(read_only=True)
    category = CategorySerializer(read_only=True)
    subcategory = SubcategorySerializer(read_only=True)
    brand = BrandSerializer(read_only=True)
    images = ProductImageSerializer(many=True, read_only=True)
    variants = ProductVariantSerializer(many=True, read_only=True)
    discount_percent = serializers.ReadOnlyField()
    related_products = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "name",
            "slug",
            "description",
            "price",
            "original_price",
            "discount_percent",
            "sku",
            "stock_qty",
            "in_stock",
            "avg_rating",
            "review_count",
            "view_count",
            "sold_count",
            "is_featured",
            "seller",
            "category",
            "subcategory",
            "brand",
            "images",
            "variants",
            "related_products",
        ]

    @extend_schema_field(ProductListSerializer(many=True))
    def get_related_products(self, obj):
        related = (
            Product.objects.filter(category=obj.category, is_active=True)
            .exclude(id=obj.id)
            .order_by("-sold_count")[:6]
        )
        return ProductListSerializer(related, many=True, context=self.context).data


# Storefront-sized product page, matching web/views.py::seller_detail_view's
# own limit - a client that needs to page through more than this should use
# GET /catalog/products/?seller=<slug> instead, which is fully paginated.
SELLER_DETAIL_PRODUCT_LIMIT = 24


class SellerDetailSerializer(SellerSerializer):
    product_count = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()

    class Meta(SellerSerializer.Meta):
        fields = SellerSerializer.Meta.fields + ["support_phone", "product_count", "products"]

    def get_product_count(self, seller) -> int:
        return seller.products.filter(is_active=True).count()

    @extend_schema_field(ProductListSerializer(many=True))
    def get_products(self, seller):
        products = (
            seller.products.filter(is_active=True)
            .select_related("category", "brand")
            .prefetch_related("images")
            .order_by("-sold_count")[:SELLER_DETAIL_PRODUCT_LIMIT]
        )
        return ProductListSerializer(products, many=True, context=self.context).data


class FlashDealSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)
    percent_stock_sold = serializers.ReadOnlyField()

    class Meta:
        model = FlashDeal
        fields = [
            "id",
            "product",
            "deal_price",
            "stock_qty",
            "stock_sold",
            "percent_stock_sold",
            "starts_at",
            "ends_at",
        ]


class CollectionSerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(source="products.count", read_only=True)
    banner_image = serializers.CharField(source="resolved_banner_url", read_only=True, allow_null=True)

    class Meta:
        model = Collection
        fields = ["id", "title", "slug", "banner_image", "linked_category", "display_order", "product_count"]


class BannerSerializer(serializers.ModelSerializer):
    image = serializers.CharField(source="resolved_image_url", read_only=True, allow_null=True)

    class Meta:
        model = Banner
        fields = ["id", "title", "subtitle", "image", "cta_label", "cta_link", "display_order"]
