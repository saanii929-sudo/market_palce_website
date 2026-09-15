from django.contrib import admin

from .models import DeliveryMethod, Order, OrderItem, OrderStatusHistory, Payment, PaymentMethod, PendingCheckout, Shipment


@admin.register(DeliveryMethod)
class DeliveryMethodAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "code", "price", "eta_days_min", "eta_days_max", "is_active"]
    search_fields = ["name", "code"]


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "code", "is_active"]
    search_fields = ["name", "code"]


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "product", "variant", "qty", "unit_price"]
    search_fields = ["order__order_number", "product__name"]
    autocomplete_fields = ["order", "product", "variant"]


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


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ["order_number", "user", "status", "total", "placed_at"]
    list_filter = ["status", "delivery_method", "payment_method"]
    search_fields = ["order_number", "user__email", "user__phone"]
    autocomplete_fields = ["user", "coupon", "delivery_method", "payment_method", "delivery_address"]
    readonly_fields = ["order_number", "subtotal", "delivery_fee", "discount_amount", "total", "placed_at"]
    inlines = [OrderItemInline, OrderStatusHistoryInline, PaymentInline]


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "courier_name", "tracking_number", "current_status"]
    autocomplete_fields = ["order"]


@admin.register(PendingCheckout)
class PendingCheckoutAdmin(admin.ModelAdmin):
    list_display = ["reference", "user", "total", "status", "created_at", "order"]
    list_filter = ["status"]
    search_fields = ["reference", "user__email", "user__phone"]
    autocomplete_fields = ["user", "address", "delivery_method", "payment_method", "coupon", "order"]
    readonly_fields = ["reference", "cart_snapshot", "checkout_url"]
