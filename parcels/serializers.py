from rest_framework import serializers

from accounts.models import Address

from .models import Parcel


class ParcelQuoteSerializer(serializers.Serializer):
    package_size = serializers.ChoiceField(choices=Parcel.PackageSize.choices)
    pickup_lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    pickup_lng = serializers.DecimalField(max_digits=9, decimal_places=6)
    dropoff_lat = serializers.DecimalField(max_digits=9, decimal_places=6)
    dropoff_lng = serializers.DecimalField(max_digits=9, decimal_places=6)


class ParcelQuoteResponseSerializer(serializers.Serializer):
    distance_km = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)


class ParcelCreateSerializer(serializers.Serializer):
    recipient_name = serializers.CharField(max_length=150)
    recipient_phone = serializers.CharField(max_length=20)
    package_size = serializers.ChoiceField(choices=Parcel.PackageSize.choices)

    pickup_address_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    pickup_line1 = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    pickup_city = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    pickup_lat = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True, default=None
    )
    pickup_lng = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True, default=None
    )

    dropoff_line1 = serializers.CharField(max_length=255)
    dropoff_city = serializers.CharField(max_length=100)
    dropoff_lat = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True, default=None
    )
    dropoff_lng = serializers.DecimalField(
        max_digits=9, decimal_places=6, required=False, allow_null=True, default=None
    )

    description = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    photo = serializers.ImageField(required=False, allow_null=True, default=None)
    declared_value = serializers.DecimalField(
        max_digits=10, decimal_places=2, required=False, allow_null=True, default=None
    )

    def validate_pickup_address_id(self, value):
        if value is None:
            return None
        try:
            return Address.objects.get(id=value, user=self.context["request"].user)
        except Address.DoesNotExist:
            raise serializers.ValidationError("Select a valid pickup address.")


class ParcelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Parcel
        fields = [
            "id", "recipient_name", "recipient_phone",
            "pickup_line1", "pickup_city", "dropoff_line1", "dropoff_city",
            "package_size", "description", "photo", "declared_value",
            "status", "price", "created_at",
        ]
        read_only_fields = fields
