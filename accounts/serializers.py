from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from catalog.models import Category
from catalog.serializers import CategorySerializer

from .models import Address, OTPCode, UserInterest

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, validators=[validate_password])

    class Meta:
        model = User
        fields = ["id", "email", "phone", "full_name", "password"]

    def validate(self, attrs):
        if not attrs.get("email") and not attrs.get("phone"):
            raise serializers.ValidationError("Provide at least an email or a phone number.")
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user


class OTPSendSerializer(serializers.Serializer):
    destination = serializers.CharField()
    purpose = serializers.ChoiceField(choices=OTPCode.Purpose.choices)


class OTPVerifySerializer(serializers.Serializer):
    destination = serializers.CharField()
    purpose = serializers.ChoiceField(choices=OTPCode.Purpose.choices)
    code = serializers.CharField(max_length=6, min_length=6)


class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(help_text="Email or phone number")
    password = serializers.CharField(write_only=True)


class SocialAuthSerializer(serializers.Serializer):
    token = serializers.CharField()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class PasswordForgotSerializer(serializers.Serializer):
    destination = serializers.CharField(help_text="Email or phone number")


class PasswordResetSerializer(serializers.Serializer):
    destination = serializers.CharField()
    code = serializers.CharField(max_length=6, min_length=6)
    new_password = serializers.CharField(write_only=True, validators=[validate_password])


class UserSerializer(serializers.ModelSerializer):
    interests = CategorySerializer(many=True, read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "email",
            "phone",
            "full_name",
            "avatar",
            "bio",
            "role",
            "is_email_verified",
            "is_phone_verified",
            "push_notifications_enabled",
            "email_offers_enabled",
            "interests",
            "date_joined",
        ]
        read_only_fields = ["id", "email", "phone", "role", "is_email_verified", "is_phone_verified", "date_joined"]


class UserInterestUpdateSerializer(serializers.Serializer):
    category_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=True)

    def validate_category_ids(self, value):
        existing = set(Category.objects.filter(id__in=value, is_active=True).values_list("id", flat=True))
        missing = set(value) - existing
        if missing:
            raise serializers.ValidationError(f"Unknown category ids: {sorted(missing)}")
        return value

    def save(self, **kwargs):
        user = self.context["request"].user
        category_ids = self.validated_data["category_ids"]
        UserInterest.objects.filter(user=user).exclude(category_id__in=category_ids).delete()
        existing_ids = set(UserInterest.objects.filter(user=user).values_list("category_id", flat=True))
        new_interests = [
            UserInterest(user=user, category_id=cid) for cid in category_ids if cid not in existing_ids
        ]
        UserInterest.objects.bulk_create(new_interests)
        return user


class AvatarUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["avatar"]


class AddressSerializer(serializers.ModelSerializer):
    class Meta:
        model = Address
        fields = [
            "id",
            "label",
            "recipient_name",
            "phone",
            "line1",
            "line2",
            "city",
            "region",
            "country",
            "is_default",
        ]

    def create(self, validated_data):
        validated_data["user"] = self.context["request"].user
        return super().create(validated_data)
