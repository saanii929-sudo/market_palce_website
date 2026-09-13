from rest_framework import serializers

from orders.models import Order, OrderItem

from .models import Review


class ReviewSerializer(serializers.ModelSerializer):
    """Read serializer used both here and embedded in the catalog product
    reviews listing."""

    user_name = serializers.CharField(source="user.full_name", read_only=True)

    class Meta:
        model = Review
        fields = ["id", "user_name", "product", "rating", "comment", "created_at"]
        read_only_fields = fields


class ReviewCreateSerializer(serializers.Serializer):
    order_item_id = serializers.IntegerField()
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(required=False, allow_blank=True, default="")

    def validate_order_item_id(self, value):
        request = self.context["request"]
        try:
            order_item = OrderItem.objects.select_related("order", "product").get(
                id=value, order__user=request.user
            )
        except OrderItem.DoesNotExist:
            raise serializers.ValidationError("Order item not found.")

        if order_item.order.status != Order.Status.DELIVERED:
            raise serializers.ValidationError("You can only review items from delivered orders.")

        if Review.objects.filter(order_item=order_item).exists():
            raise serializers.ValidationError("You've already reviewed this purchase.")

        return order_item

    def create(self, validated_data):
        order_item = validated_data["order_item_id"]
        return Review.objects.create(
            user=self.context["request"].user,
            product=order_item.product,
            order_item=order_item,
            rating=validated_data["rating"],
            comment=validated_data["comment"],
        )


class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "comment"]

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value
