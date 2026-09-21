from rest_framework import serializers

from orders.models import OrderItem, SellerOrder

from .models import Review, ReviewFlag


class ReviewSerializer(serializers.ModelSerializer):
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
            order_item = OrderItem.objects.select_related("seller_order__order", "product").get(
                id=value, seller_order__order__user=request.user
            )
        except OrderItem.DoesNotExist:
            raise serializers.ValidationError("Order item not found.")

        if order_item.seller_order.status != SellerOrder.Status.DELIVERED:
            raise serializers.ValidationError("You can only review items from delivered orders.")

        if Review.objects.filter(order_item=order_item).exists():
            raise serializers.ValidationError("You've already reviewed this purchase.")

        return order_item

    def create(self, validated_data):
        from django.db import transaction

        from risk.tasks import check_review_farming

        order_item = validated_data["order_item_id"]
        review = Review.objects.create(
            user=self.context["request"].user,
            product=order_item.product,
            order_item=order_item,
            rating=validated_data["rating"],
            comment=validated_data["comment"],
        )
        transaction.on_commit(lambda: check_review_farming.delay(review.id))
        return review


class ReviewUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Review
        fields = ["rating", "comment"]

    def validate_rating(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError("Rating must be between 1 and 5.")
        return value


class ReviewFlagCreateSerializer(serializers.Serializer):
    reason = serializers.ChoiceField(choices=ReviewFlag.Reason.choices)


class AdminReviewSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.full_name", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)

    class Meta:
        model = Review
        fields = [
            "id",
            "user",
            "user_name",
            "product",
            "product_name",
            "rating",
            "comment",
            "status",
            "flagged_count",
            "moderation_note",
            "created_at",
        ]
        read_only_fields = ["id", "user", "user_name", "product", "product_name", "rating", "comment", "flagged_count", "created_at"]


class ReviewModerateSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["keep", "remove"])
    moderation_note = serializers.CharField(required=False, allow_blank=True, default="")
