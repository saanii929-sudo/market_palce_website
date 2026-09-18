from rest_framework import serializers

from accounts.models import Address
from cart.serializers import CartItemSerializer
from catalog.models import Product

from .models import DeliveryMethod, Order, OrderItem, OrderStatusHistory, Payment, PaymentMethod, PendingCheckout, Shipment


class DeliveryMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryMethod
        fields = ["id", "name", "code", "price", "eta_days_min", "eta_days_max"]


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "name", "code"]


class WebhookResponseSerializer(serializers.Serializer):
    """Documents the {"detail": "..."} shape both payment webhooks (order
    checkout and, in the sellers app, subscription checkout) always return,
    replacing drf-spectacular's generic free-form object fallback."""

    detail = serializers.CharField()


class CheckoutSummarySerializer(serializers.Serializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    coupon_error = serializers.CharField(read_only=True, allow_null=True)


class PlaceOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    delivery_method_id = serializers.IntegerField()
    payment_method_id = serializers.IntegerField()
    # Only used when the resolved payment method is a hosted-checkout gateway
    # (e.g. Hubtel) - where the customer's browser should land after paying.
    # Falls back to HUBTEL_RETURN_URL/HUBTEL_CANCELLATION_URL if omitted.
    return_url = serializers.URLField(required=False, allow_blank=True)
    cancellation_url = serializers.URLField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context["request"]

        try:
            attrs["address"] = Address.objects.get(id=attrs["address_id"], user=request.user)
        except Address.DoesNotExist:
            raise serializers.ValidationError("Address not found.")

        try:
            attrs["delivery_method"] = DeliveryMethod.objects.get(id=attrs["delivery_method_id"], is_active=True)
        except DeliveryMethod.DoesNotExist:
            raise serializers.ValidationError("Delivery method not found.")

        try:
            attrs["payment_method"] = PaymentMethod.objects.get(id=attrs["payment_method_id"], is_active=True)
        except PaymentMethod.DoesNotExist:
            raise serializers.ValidationError("Payment method not found.")

        return attrs


class OrderItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    line_total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = OrderItem
        fields = ["id", "product", "product_name", "product_slug", "variant", "qty", "unit_price", "line_total"]


class ShipmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Shipment
        fields = ["courier_name", "tracking_number", "current_status"]


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ["id", "gateway", "gateway_reference", "status", "amount", "created_at"]


class OrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ["order_number", "status", "total", "placed_at"]


class OrderDetailSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_method = DeliveryMethodSerializer(read_only=True)
    payment_method = PaymentMethodSerializer(read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "order_number",
            "status",
            "subtotal",
            "delivery_fee",
            "discount_amount",
            "total",
            "delivery_method",
            "payment_method",
            "delivery_recipient_name",
            "delivery_phone",
            "delivery_line1",
            "delivery_line2",
            "delivery_city",
            "delivery_region",
            "delivery_country",
            "items",
            "payments",
            "placed_at",
        ]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ["status", "note", "created_at"]


class OrderTrackingSerializer(serializers.ModelSerializer):
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)
    shipment = ShipmentSerializer(read_only=True)

    class Meta:
        model = Order
        fields = ["order_number", "status", "status_history", "shipment"]


class PendingCheckoutSerializer(serializers.ModelSerializer):
    order = OrderDetailSerializer(read_only=True)

    class Meta:
        model = PendingCheckout
        fields = ["reference", "checkout_url", "status", "failure_reason", "order"]
