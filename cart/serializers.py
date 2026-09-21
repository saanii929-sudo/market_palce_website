from rest_framework import serializers

from catalog.models import Product, ProductVariant

from .models import CartItem, Coupon


class CartItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    product_slug = serializers.CharField(source="product.slug", read_only=True)
    unit_price = serializers.DecimalField(source="product.price", max_digits=10, decimal_places=2, read_only=True)
    line_total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    variant_label = serializers.SerializerMethodField()

    class Meta:
        model = CartItem
        fields = [
            "id",
            "product",
            "product_name",
            "product_slug",
            "variant",
            "variant_label",
            "qty",
            "unit_price",
            "line_total",
        ]

    def get_variant_label(self, obj) -> str | None:
        if not obj.variant:
            return None
        bits = [b for b in [obj.variant.size, obj.variant.color] if b]
        return ", ".join(bits) or None


class CartItemAddSerializer(serializers.Serializer):
    product_id = serializers.IntegerField()
    variant_id = serializers.IntegerField(required=False, allow_null=True)
    qty = serializers.IntegerField(min_value=1, default=1)

    def validate(self, attrs):
        try:
            product = Product.objects.get(id=attrs["product_id"], is_active=True)
        except Product.DoesNotExist:
            raise serializers.ValidationError("Product not found.")

        variant = None
        variant_id = attrs.get("variant_id")
        if variant_id is not None:
            try:
                variant = ProductVariant.objects.get(id=variant_id, product=product)
            except ProductVariant.DoesNotExist:
                raise serializers.ValidationError("Variant not found for this product.")

        attrs["product"] = product
        attrs["variant"] = variant
        return attrs


class CartItemUpdateSerializer(serializers.Serializer):
    qty = serializers.IntegerField(min_value=1)


class CouponApplySerializer(serializers.Serializer):
    code = serializers.CharField()

    def validate_code(self, value):
        try:
            return Coupon.objects.get(code__iexact=value)
        except Coupon.DoesNotExist:
            raise serializers.ValidationError("Invalid coupon code.")


class PromotionSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source="seller.business_name", read_only=True, default=None)
    seller_slug = serializers.CharField(source="seller.slug", read_only=True, default=None)

    class Meta:
        model = Coupon
        fields = [
            "code",
            "scope",
            "seller_name",
            "seller_slug",
            "title",
            "description",
            "banner_image",
            "discount_type",
            "value",
            "min_order_amount",
            "max_discount_amount",
            "valid_to",
        ]
        read_only_fields = fields


class SellerCartGroupSerializer(serializers.Serializer):
    seller_id = serializers.IntegerField(source="seller.id")
    seller_name = serializers.CharField(source="seller.business_name")
    seller_slug = serializers.CharField(source="seller.slug")
    items = CartItemSerializer(many=True, read_only=True)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    free_delivery_threshold_met = serializers.BooleanField(read_only=True)


class CartSerializer(serializers.Serializer):
    items = CartItemSerializer(many=True, read_only=True)
    seller_groups = SellerCartGroupSerializer(many=True, read_only=True)
    grand_subtotal = serializers.DecimalField(source="subtotal", max_digits=10, decimal_places=2, read_only=True)
    discount_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    delivery_fee = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    grand_total = serializers.DecimalField(source="total", max_digits=10, decimal_places=2, read_only=True)
    coupon_code = serializers.SerializerMethodField()
    coupon_error = serializers.CharField(read_only=True, allow_null=True)

    def get_coupon_code(self, obj) -> str | None:
        cart = self.context.get("cart")
        return cart.applied_coupon.code if cart and cart.applied_coupon else None
