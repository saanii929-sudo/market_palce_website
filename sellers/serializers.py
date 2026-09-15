from decimal import Decimal

from rest_framework import serializers

from catalog.models import Category

from .models import Payout, SellerApplication


class SellerApplicationSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)

    class Meta:
        model = SellerApplication
        fields = [
            "id",
            "business_name",
            "category",
            "category_name",
            "phone",
            "id_document",
            "business_certificate",
            "status",
            "submitted_at",
            "reviewed_at",
            "reviewer_note",
        ]
        read_only_fields = ["id", "status", "submitted_at", "reviewed_at", "reviewer_note"]


class SellerApplicationCreateSerializer(serializers.Serializer):
    business_name = serializers.CharField(max_length=150)
    category_id = serializers.IntegerField()
    phone = serializers.CharField(max_length=20)
    id_document = serializers.FileField()
    business_certificate = serializers.FileField(required=False)

    def validate_category_id(self, value):
        try:
            return Category.objects.get(id=value, is_active=True)
        except Category.DoesNotExist:
            raise serializers.ValidationError("Category not found.")


class SellerApplicationReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject"])
    reviewer_note = serializers.CharField(required=False, allow_blank=True, default="")


class PayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payout
        fields = [
            "id", "amount", "method", "account_details", "status",
            "created_at", "payout_date", "admin_note",
        ]
        read_only_fields = fields


class PayoutRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))
    method = serializers.CharField(max_length=50)
    account_details = serializers.CharField(max_length=255)
