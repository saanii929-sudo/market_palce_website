from django.contrib import admin

from .models import (
    DeliveryMethod,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    PaymentMethod,
    PendingCheckout,
    RefundRequest,
    RefundStatusHistory,
    SellerOrder,
    Shipment,
    TaxRule,
)


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "code", "price", "eta_days_min", "eta_days_max", "is_active"]
    search_fields = ["name", "code"]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "code", "is_active"]
    search_fields = ["name", "code"]


@admin.register(TaxRule)
class TaxRuleAdmin(admin.ModelAdmin):
    list_display = ["region", "category", "rate", "effective_from"]
    list_filter = ["region", "category"]
    autocomplete_fields = ["category"]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["id", "seller_order", "product", "variant", "qty", "unit_price"]
    search_fields = ["seller_order__suborder_number", "product__name"]
    autocomplete_fields = ["seller_order", "product", "variant"]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    autocomplete_fields = ["product", "variant"]
    readonly_fields = ["product", "variant", "qty", "unit_price"]
    can_delete = False


class OrderStatusHistoryInline(admin.TabularInline):
    model = OrderStatusHistory
    extra = 0
    readonly_fields = ["status", "note", "created_at"]
    can_delete = False


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    readonly_fields = ["gateway", "gateway_reference", "status", "amount", "created_at"]
    can_delete = False


class SellerOrderInline(admin.TabularInline):
    model = SellerOrder
    extra = 0
    fields = ["suborder_number", "seller", "status", "subtotal", "tax_amount", "delivery_fee", "discount_amount", "total"]
    readonly_fields = fields
    can_delete = False
    show_change_link = True


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "user", "display_status", "display_total", "placed_at"]
    search_fields = ["order_number", "user__email", "user__phone"]
    autocomplete_fields = ["user", "coupon", "delivery_address"]
    readonly_fields = ["order_number", "placed_at"]
    inlines = [SellerOrderInline, PaymentInline]

    @admin.display(description="Status")
    def display_status(self, obj):
        return obj.get_status_display()

    @admin.display(description="Total")
    def display_total(self, obj):
        return obj.total


@admin.register(SellerOrder)
class SellerOrderAdmin(admin.ModelAdmin):
    list_display = ["suborder_number", "order", "seller", "status", "total", "created_at"]
    list_filter = ["status", "seller"]
    search_fields = ["suborder_number", "order__order_number", "seller__business_name"]
    autocomplete_fields = ["order", "seller", "delivery_method"]
    readonly_fields = ["suborder_number", "subtotal", "tax_amount", "delivery_fee", "discount_amount", "total"]
    inlines = [OrderItemInline, OrderStatusHistoryInline]


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["id", "seller_order", "courier_name", "tracking_number", "current_status"]
    autocomplete_fields = ["seller_order"]


@admin.register(PendingCheckout)
class PendingCheckoutAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "total", "status", "created_at", "order"]
    list_filter = ["status"]
    search_fields = ["reference", "user__email", "user__phone"]
    autocomplete_fields = ["user", "address", "delivery_method", "payment_method", "coupon", "order"]
    readonly_fields = ["reference", "cart_snapshot", "checkout_url"]


class RefundStatusHistoryInline(admin.TabularInline):
    model = RefundStatusHistory
    extra = 0
    readonly_fields = ["status", "note", "actor", "created_at"]
    can_delete = False


@admin.register(RefundRequest)
class RefundRequestAdmin(admin.ModelAdmin):
    list_display = ["id", "order_item", "requested_by", "reason", "status", "refund_amount", "requested_at"]
    list_filter = ["status", "reason", "refund_type"]
    search_fields = ["order_item__seller_order__order__order_number", "requested_by__email", "requested_by__phone"]
    autocomplete_fields = ["order_item", "requested_by"]
    readonly_fields = ["requested_at"]
    inlines = [RefundStatusHistoryInline]


