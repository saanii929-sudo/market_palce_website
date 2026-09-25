from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import RiderDocument, RiderEarning, RiderPayout, RiderPayoutAccount, RiderProfile, Vehicle
from .services import required_document_types


class RiderRegisterSerializer(serializers.Serializer):
    email = serializers.EmailField(required=False, allow_blank=True, default="")
    phone = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    full_name = serializers.CharField(max_length=150, required=False, allow_blank=True, default="")
    password = serializers.CharField(write_only=True, validators=[validate_password])
    vehicle_type = serializers.ChoiceField(choices=Vehicle.Type.choices)
    vehicle_make = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    vehicle_model = serializers.CharField(max_length=50, required=False, allow_blank=True, default="")
    plate_number = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")
    color = serializers.CharField(max_length=30, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError("Provide at least an email or a phone number.")
        return attrs

    def validate_email(self, value):
        from accounts.models import User

        if value and User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate_phone(self, value):
        from accounts.models import User

        if value and User.objects.filter(phone=value).exists():
            raise serializers.ValidationError("An account with this phone number already exists.")
        return value


class VehicleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = ["id", "type", "make", "model", "plate_number", "color", "photo", "created_at"]
        read_only_fields = ["id", "created_at"]


class VehicleUpdateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=Vehicle.Type.choices, required=False)
    make = serializers.CharField(max_length=50, required=False, allow_blank=True)
    model = serializers.CharField(max_length=50, required=False, allow_blank=True)
    plate_number = serializers.CharField(max_length=20, required=False, allow_blank=True)
    color = serializers.CharField(max_length=30, required=False, allow_blank=True)
    photo = serializers.ImageField(required=False)


class RiderDocumentSerializer(serializers.ModelSerializer):
    doc_type_display = serializers.CharField(source="get_doc_type_display", read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = RiderDocument
        fields = [
            "id", "doc_type", "doc_type_display", "file", "status", "expires_at",
            "reviewer_note", "reviewed_at", "is_expired", "created_at",
        ]
        read_only_fields = ["id", "status", "reviewer_note", "reviewed_at", "is_expired", "created_at"]


class RiderDocumentUploadSerializer(serializers.Serializer):
    doc_type = serializers.ChoiceField(choices=RiderDocument.DocType.choices)
    file = serializers.FileField()
    expires_at = serializers.DateField(required=False, allow_null=True, default=None)


class RiderDocumentReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["verify", "reject"])
    reviewer_note = serializers.CharField(required=False, allow_blank=True, default="")


class RiderProfileSerializer(serializers.ModelSerializer):
    email = serializers.EmailField(source="user.email", read_only=True)
    phone = serializers.CharField(source="user.phone", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    push_notifications_enabled = serializers.BooleanField(source="user.push_notifications_enabled", read_only=True)
    email_offers_enabled = serializers.BooleanField(source="user.email_offers_enabled", read_only=True)

    class Meta:
        model = RiderProfile
        fields = [
            "id", "email", "phone", "full_name", "rating_avg", "is_online", "is_verified",
            "current_lat", "current_lng", "acceptance_rate", "min_trip_value",
            "push_notifications_enabled", "email_offers_enabled",
        ]
        read_only_fields = [
            "id", "rating_avg", "is_online", "is_verified", "current_lat", "current_lng", "acceptance_rate",
        ]


class RiderSettingsSerializer(serializers.Serializer):
    min_trip_value = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    push_notifications_enabled = serializers.BooleanField(required=False)
    email_offers_enabled = serializers.BooleanField(required=False)


class VerificationStatusSerializer(serializers.Serializer):
    is_verified = serializers.BooleanField()
    documents = serializers.ListField(child=serializers.DictField())

    @classmethod
    def build(cls, rider_profile: RiderProfile) -> dict:
        required = required_document_types(rider_profile)
        documents_by_type = {d.doc_type: d for d in rider_profile.documents.all()}
        doc_type_labels = dict(RiderDocument.DocType.choices)

        documents = []
        for doc_type in required:
            document = documents_by_type.get(doc_type)
            documents.append({
                "doc_type": doc_type,
                "doc_type_display": doc_type_labels.get(doc_type, doc_type),
                "status": document.status if document else "missing",
                "reviewer_note": document.reviewer_note if document else "",
                "expires_at": document.expires_at if document else None,
                "is_expired": document.is_expired if document else False,
            })

        return {"is_verified": rider_profile.is_verified, "documents": documents}


class RiderOnlineToggleSerializer(serializers.Serializer):
    is_online = serializers.BooleanField()


class LocationPingSerializer(serializers.Serializer):
    lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    lng = serializers.DecimalField(max_digits=9, decimal_places=6)


class EarningsSummarySerializer(serializers.Serializer):
    period = serializers.CharField()
    start_date = serializers.DateField()
    total_earnings = serializers.DecimalField(max_digits=10, decimal_places=2)
    trip_count = serializers.IntegerField()
    daily_breakdown = serializers.ListField(child=serializers.DictField())


class RiderEarningSerializer(serializers.ModelSerializer):
    delivery_id = serializers.IntegerField(source="trip.delivery_id", read_only=True)

    class Meta:
        model = RiderEarning
        fields = [
            "id", "trip", "delivery_id", "base_fare", "distance_bonus", "tip", "surge_multiplier",
            "total", "created_at",
        ]
        read_only_fields = fields


class RiderPayoutRequestSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    payout_account_id = serializers.IntegerField()

    def validate_payout_account_id(self, value):
        try:
            return RiderPayoutAccount.objects.get(id=value)
        except RiderPayoutAccount.DoesNotExist:
            raise serializers.ValidationError("Select a valid payout account.")


class RiderPayoutSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiderPayout
        fields = ["id", "amount", "payout_account", "status", "requested_at", "completed_at", "admin_note"]
        read_only_fields = fields


class DeliveryRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=["store_order", "parcel"])
    pickup_label = serializers.CharField()
    pickup_address = serializers.CharField()
    dropoff_address = serializers.CharField()
    customer_name = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    distance_km = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    eta_minutes = serializers.IntegerField()
    item_count = serializers.IntegerField()


class ActiveDeliverySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    status = serializers.CharField()
    kind = serializers.ChoiceField(choices=["store_order", "parcel"])
    pickup_label = serializers.CharField()
    pickup_address = serializers.CharField()
    dropoff_address = serializers.CharField()
    customer_name = serializers.CharField()
    customer_phone = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    distance_km = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    picked_up_at = serializers.DateTimeField(allow_null=True)


class DeliveryCompleteSerializer(serializers.Serializer):
    delivery_code = serializers.CharField(max_length=4)


class DeliveryHistorySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.ChoiceField(choices=["store_order", "parcel"])
    order_number_or_parcel_id = serializers.CharField()
    pickup_label = serializers.CharField()
    dropoff_area = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    status = serializers.ChoiceField(choices=["delivered", "cancelled"])
    rating = serializers.IntegerField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class NewEarningsSummarySerializer(serializers.Serializer):
    available_balance = serializers.DecimalField(max_digits=10, decimal_places=2)
    today_earnings = serializers.DecimalField(max_digits=10, decimal_places=2)
    trips_today = serializers.IntegerField()
    online_hours_today = serializers.DecimalField(max_digits=5, decimal_places=2)
    week = serializers.ListField(child=serializers.DecimalField(max_digits=10, decimal_places=2))
    total_trips = serializers.IntegerField()
    avg_per_trip = serializers.DecimalField(max_digits=10, decimal_places=2)


class EarningsActivitySerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=["delivery", "cash_out"])
    label = serializers.CharField()
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()


class PayoutMethodSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    subtitle = serializers.CharField()
    is_default = serializers.BooleanField()

    @classmethod
    def build(cls, account: RiderPayoutAccount) -> dict:
        return {
            "id": account.id,
            "title": account.provider or account.get_type_display(),
            "subtitle": account.masked_number,
            "is_default": account.is_default,
        }


class PayoutMethodCreateSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=100)
    account_number = serializers.CharField(max_length=50, write_only=True)
    type = serializers.ChoiceField(choices=RiderPayoutAccount.Type.choices, required=False, default=RiderPayoutAccount.Type.MOMO)


class CashOutSerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    payout_method_id = serializers.IntegerField()

    def validate_payout_method_id(self, value):
        try:
            return RiderPayoutAccount.objects.get(id=value)
        except RiderPayoutAccount.DoesNotExist:
            raise serializers.ValidationError("Select a valid payout method.")


class RiderReviewSerializer(serializers.Serializer):
    reviewer_name = serializers.SerializerMethodField()
    rating = serializers.IntegerField(source="stars")
    comment = serializers.CharField()
    created_at = serializers.DateTimeField()

    def get_reviewer_name(self, obj) -> str:
        customer = obj.customer
        return customer.full_name or customer.email or customer.phone or "Customer"


class RiderReviewsSummarySerializer(serializers.Serializer):
    average = serializers.DecimalField(max_digits=3, decimal_places=2)
    breakdown = serializers.DictField(child=serializers.FloatField())


class RateRiderSubmitSerializer(serializers.Serializer):
    rating = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
