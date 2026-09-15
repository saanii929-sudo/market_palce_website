from django.contrib import admin

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


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "slug", "display_order", "commission_rate", "is_active"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]


@admin.register(Subcategory)
class SubcategoryAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "category", "display_order", "is_active"]
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ["category"]
    search_fields = ["name"]


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "slug", "is_active"]
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ["name"]


@admin.register(Seller)
class SellerAdmin(admin.ModelAdmin):
    list_display = ["id", "business_name", "user", "rating", "is_verified", "is_featured"]
    prepopulated_fields = {"slug": ("business_name",)}
    search_fields = ["business_name"]
    autocomplete_fields = ["user"]


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "seller", "category", "price", "stock_qty", "is_active", "is_featured", "sold_count"]
    list_filter = ["is_active", "is_featured", "category", "brand"]
    search_fields = ["name", "sku"]
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ["seller", "category", "subcategory", "brand"]
    inlines = [ProductImageInline, ProductVariantInline]
    readonly_fields = ["avg_rating", "review_count", "view_count", "sold_count"]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "size", "color", "stock_qty"]
    search_fields = ["product__name", "sku"]
    autocomplete_fields = ["product"]


@admin.register(FlashDeal)
class FlashDealAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "deal_price", "stock_qty", "stock_sold", "starts_at", "ends_at", "is_active"]
    list_filter = ["is_active"]
    autocomplete_fields = ["product"]


@admin.register(Collection)
class CollectionAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "linked_category", "display_order", "is_active"]
    prepopulated_fields = {"slug": ("title",)}
    autocomplete_fields = ["linked_category", "products"]


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "display_order", "is_active", "active_from", "active_to"]
