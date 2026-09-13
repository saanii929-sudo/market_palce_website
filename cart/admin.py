from django.contrib import admin

from .models import Cart, CartItem, Coupon


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    autocomplete_fields = ["product", "variant"]


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "session_key", "applied_coupon", "created_at"]
    search_fields = ["user__email", "session_key"]
    autocomplete_fields = ["user", "applied_coupon"]
    inlines = [CartItemInline]


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ["id", "code", "discount_type", "value", "is_active", "times_used", "usage_limit"]
    search_fields = ["code"]
