from decimal import Decimal

from rest_framework import serializers

from cart.models import Coupon
from catalog.models import Category

from .models import BulkUploadJob, Payout, SellerApplication, SellerFavoriteRider


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
            "bank_account_name",
            "bank_account_number",
            "bank_name",
            "momo_number",
            "momo_network",
            "kyc_status",
            "kyc_reviewed_at",
        ]
        read_only_fields = [
            "id", "status", "submitted_at", "reviewed_at", "reviewer_note", "kyc_status", "kyc_reviewed_at",
        ]


class SellerKYCSubmitSerializer(serializers.Serializer):
    bank_account_name = serializers.CharField(required=False, allow_blank=True, default="")
    bank_account_number = serializers.CharField(required=False, allow_blank=True, default="")
    bank_name = serializers.CharField(required=False, allow_blank=True, default="")
    momo_number = serializers.CharField(required=False, allow_blank=True, default="")
    momo_network = serializers.CharField(required=False, allow_blank=True, default="")


class SellerKYCReviewSerializer(serializers.Serializer):
    reviewer_note = serializers.CharField(required=False, allow_blank=True, default="")


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


class SellerCouponSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "id",
            "code",
            "scope",
            "discount_type",
            "value",
            "min_order_amount",
            "max_discount_amount",
            "valid_from",
            "valid_to",
            "usage_limit",
            "per_user_limit",
            "times_used",
            "is_active",
            "is_public",
            "title",
            "description",
            "banner_image",
            "created_at",
        ]
        read_only_fields = ["id", "scope", "times_used", "created_at"]


class SellerCouponCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = [
            "code",
            "discount_type",
            "value",
            "min_order_amount",
            "max_discount_amount",
            "valid_from",
            "valid_to",
            "usage_limit",
            "per_user_limit",
            "is_active",
            "is_public",
            "title",
            "description",
            "banner_image",
        ]

    def validate_code(self, value):
        if Coupon.objects.filter(code__iexact=value).exists():
            raise serializers.ValidationError("This coupon code is already in use.")
        return value


class BulkUploadJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = BulkUploadJob
        fields = [
            "id", "status", "total_rows", "success_count", "error_count", "error_report", "created_at",
        ]
        read_only_fields = fields


class NearbyRiderSerializer(serializers.Serializer):
    rider_id = serializers.IntegerField()
    name = serializers.CharField()
    rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    vehicle_type = serializers.CharField(allow_null=True)
    distance_km = serializers.DecimalField(max_digits=6, decimal_places=2)
    eta_minutes = serializers.IntegerField()


class RequestRiderSerializer(serializers.Serializer):
    mode = serializers.ChoiceField(choices=["auto", "direct"])
    rider_id = serializers.IntegerField(required=False, allow_null=True, default=None)

    def validate_rider_id(self, value):
        if value is None:
            return None
        from riders.models import RiderProfile

        try:
            return RiderProfile.objects.get(id=value)
        except RiderProfile.DoesNotExist:
            raise serializers.ValidationError("Select a valid rider.")

    def validate(self, attrs):
        if attrs["mode"] == "direct" and attrs.get("rider_id") is None:
            raise serializers.ValidationError({"rider_id": "rider_id is required for direct mode."})
        return attrs


class SellerFavoriteRiderCreateSerializer(serializers.Serializer):
    rider_id = serializers.IntegerField()
    notes = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")

    def validate_rider_id(self, value):
        from riders.models import RiderProfile

        try:
            return RiderProfile.objects.get(id=value)
        except RiderProfile.DoesNotExist:
            raise serializers.ValidationError("Select a valid rider.")


class SellerFavoriteRiderSerializer(serializers.ModelSerializer):
    rider_id = serializers.IntegerField(source="rider.id", read_only=True)
    rider_name = serializers.SerializerMethodField()
    rider_rating = serializers.DecimalField(source="rider.rating_avg", max_digits=3, decimal_places=2, read_only=True)

    class Meta:
        model = SellerFavoriteRider
        fields = ["id", "rider_id", "rider_name", "rider_rating", "notes", "created_at"]
        read_only_fields = fields

    def get_rider_name(self, obj):
        return obj.rider.user.full_name or obj.rider.user.email or obj.rider.user.phone
