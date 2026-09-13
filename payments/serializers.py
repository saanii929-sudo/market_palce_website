from rest_framework import serializers

from .models import PaymentMethodToken


class PaymentMethodTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = PaymentMethodToken
        fields = ["id", "gateway", "brand", "last4", "expiry_month", "expiry_year", "is_default", "created_at"]
        read_only_fields = ["id", "created_at"]


class PaymentMethodTokenCreateSerializer(serializers.ModelSerializer):
    """Accepts the token + display fields the gateway's client-side SDK
    already returned after tokenizing - never raw card data."""

    class Meta:
        model = PaymentMethodToken
        fields = ["gateway", "token", "brand", "last4", "expiry_month", "expiry_year"]
