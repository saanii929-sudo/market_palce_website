from rest_framework import serializers

from .models import Delivery, DeliveryOffer, ProofOfDelivery, Trip


class DeliverySerializer(serializers.ModelSerializer):
    no_riders_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = Delivery
        fields = [
            "id", "delivery_type", "pickup_address", "pickup_contact_name", "pickup_contact_phone",
            "dropoff_address", "dropoff_contact_name", "dropoff_contact_phone",
            "distance_km", "price", "status", "dispatch_attempts", "no_riders_available", "created_at",
        ]
        read_only_fields = fields


class TripSerializer(serializers.ModelSerializer):
    delivery = DeliverySerializer(read_only=True)

    class Meta:
        model = Trip
        fields = ["id", "delivery", "status", "picked_up_at", "delivered_at", "created_at"]
        read_only_fields = fields


class DeliveryOfferSerializer(serializers.ModelSerializer):
    delivery = DeliverySerializer(read_only=True)
    seconds_remaining = serializers.IntegerField(read_only=True)

    class Meta:
        model = DeliveryOffer
        fields = ["id", "delivery", "sent_at", "expires_at", "seconds_remaining", "status"]
        read_only_fields = fields


class DeliveryTrackingSerializer(serializers.Serializer):
    delivery_status = serializers.CharField()
    trip_status = serializers.CharField(allow_null=True)
    no_riders_available = serializers.BooleanField()
    rider = serializers.DictField(allow_null=True)
    delivery_otp = serializers.CharField(allow_null=True)


class ProofOfDeliverySerializer(serializers.ModelSerializer):
    class Meta:
        model = ProofOfDelivery
        fields = ["id", "otp_verified", "photo", "delivered_at", "created_at"]
        read_only_fields = fields


class ProofOfDeliverySubmitSerializer(serializers.Serializer):
    otp_code = serializers.CharField(max_length=4, required=False, allow_blank=True, default="")
    photo = serializers.ImageField(required=False, allow_null=True, default=None)


class RateRiderSerializer(serializers.Serializer):
    stars = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
