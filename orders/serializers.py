from rest_framework import serializers

from accounts.models import Address
from cart.serializers import CartItemSerializer
from catalog.models import Product
from catalog.serializers import SellerSerializer

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
)


class DeliveryMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeliveryMethod
        fields = ["id", "name", "code", "price", "eta_days_min", "eta_days_max"]


class PaymentMethodSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethod
        fields = ["id", "name", "code"]


class WebhookResponseSerializer(serializers.Serializer):
    detail = serializers.CharField()


class CheckoutSummarySerializer(serializers.Serializer):
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    tax_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    coupon_error = serializers.CharField(read_only=True, allow_null=True)


class PlaceOrderSerializer(serializers.Serializer):
    address_id = serializers.IntegerField()
    delivery_method_id = serializers.IntegerField()
    payment_method_id = serializers.IntegerField()
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


class SellerOrderSummarySerializer(serializers.ModelSerializer):
    """The lightweight per-sub-order summary embedded in OrderTrackingSerializer
    - just enough to show "which seller's shipment is at which stage" without
    the full item/shipment detail SellerOrderTrackingSerializer carries."""

    seller_name = serializers.CharField(source="seller.business_name", read_only=True)

    class Meta:
        model = SellerOrder
        fields = ["id", "suborder_number", "seller_name", "status"]


class SellerOrderSerializer(serializers.ModelSerializer):
    seller = SellerSerializer(read_only=True)
    delivery_method = DeliveryMethodSerializer(read_only=True)
    items = OrderItemSerializer(many=True, read_only=True)

    class Meta:
        model = SellerOrder
        fields = [
            "id",
            "suborder_number",
            "seller",
            "status",
            "subtotal",
            "tax_amount",
            "delivery_fee",
            "discount_amount",
            "total",
            "delivery_method",
            "items",
        ]


class OrderListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Order
        fields = ["order_number", "status", "total", "placed_at"]


class OrderDetailSerializer(serializers.ModelSerializer):
    seller_orders = SellerOrderSerializer(many=True, read_only=True)
    payments = PaymentSerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = [
            "order_number",
            "status",
            "subtotal",
            "tax_amount",
            "delivery_fee",
            "discount_amount",
            "total",
            "delivery_recipient_name",
            "delivery_phone",
            "delivery_line1",
            "delivery_line2",
            "delivery_city",
            "delivery_region",
            "delivery_country",
            "seller_orders",
            "payments",
            "placed_at",
        ]


class OrderStatusHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = OrderStatusHistory
        fields = ["status", "note", "created_at"]


class OrderTrackingSerializer(serializers.ModelSerializer):
    seller_orders = SellerOrderSummarySerializer(many=True, read_only=True)

    class Meta:
        model = Order
        fields = ["order_number", "status", "seller_orders"]


class SellerOrderTrackingSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source="seller.business_name", read_only=True)
    status_history = OrderStatusHistorySerializer(many=True, read_only=True)
    shipment = ShipmentSerializer(read_only=True)

    class Meta:
        model = SellerOrder
        fields = ["suborder_number", "seller_name", "status", "status_history", "shipment"]


class PendingCheckoutSerializer(serializers.ModelSerializer):
    order = OrderDetailSerializer(read_only=True)

    class Meta:
        model = PendingCheckout
        fields = ["reference", "checkout_url", "status", "failure_reason", "order"]


class RefundStatusHistorySerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source="actor.full_name", read_only=True, default=None)

    class Meta:
        model = RefundStatusHistory
        fields = ["status", "note", "actor_name", "created_at"]


class RefundRequestCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=RefundRequest.Reason.choices)
    reason_detail = serializers.CharField(required=False, allow_blank=True, default="")
    photos = serializers.ListField(child=serializers.URLField(), required=False, default=list)
    refund_type = serializers.ChoiceField(choices=RefundRequest.RefundType.choices, default=RefundRequest.RefundType.REFUND)


class RefundRequestSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order_item.seller_order.order.order_number", read_only=True)
    suborder_number = serializers.CharField(source="order_item.seller_order.suborder_number", read_only=True)
    product_name = serializers.CharField(source="order_item.product.name", read_only=True)
    seller_name = serializers.CharField(source="order_item.seller_order.seller.business_name", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.full_name", read_only=True)
    is_escalated = serializers.SerializerMethodField()
    status_history = RefundStatusHistorySerializer(many=True, read_only=True)

    class Meta:
        model = RefundRequest
        fields = [
            "id",
            "order_item",
            "order_number",
            "suborder_number",
            "product_name",
            "seller_name",
            "requested_by",
            "requested_by_name",
            "reason",
            "reason_detail",
            "photos",
            "refund_type",
            "status",
            "refund_amount",
            "requested_at",
            "resolved_at",
            "resolution_note",
            "is_escalated",
            "status_history",
        ]

    def get_is_escalated(self, obj) -> bool:
        return obj.is_escalated


class RefundRequestStatusUpdateSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            RefundRequest.Status.UNDER_REVIEW,
            RefundRequest.Status.APPROVED,
            RefundRequest.Status.REJECTED,
            RefundRequest.Status.REFUNDED,
        ]
    )
    note = serializers.CharField(required=False, allow_blank=True, default="")
    refund_amount = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True, default=None)


class RefundEscalateSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")
