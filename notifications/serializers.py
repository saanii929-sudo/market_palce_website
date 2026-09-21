from django.utils import timezone
from rest_framework import serializers

from .models import Broadcast, DeviceToken, Notification


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "type", "title", "body", "is_read", "created_at"]
        read_only_fields = fields


class BroadcastSerializer(serializers.ModelSerializer):
    created_by_name = serializers.CharField(source="created_by.full_name", read_only=True, default=None)
    is_sent = serializers.BooleanField(read_only=True)

    class Meta:
        model = Broadcast
        fields = [
            "id", "title", "body", "audience", "scheduled_for", "sent_at", "is_sent",
            "created_by", "created_by_name", "created_at",
        ]
        read_only_fields = ["id", "sent_at", "is_sent", "created_by", "created_by_name", "created_at"]


class BroadcastCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=150)
    body = serializers.CharField(required=False, allow_blank=True, default="")
    audience = serializers.ChoiceField(choices=Broadcast.Audience.choices, default=Broadcast.Audience.ALL)
    scheduled_for = serializers.DateTimeField(required=False, allow_null=True, default=None)

    def validate_scheduled_for(self, value):
        if value is not None and value <= timezone.now():
            raise serializers.ValidationError("scheduled_for must be in the future.")
        return value


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ["id", "token", "platform", "last_seen_at"]
        read_only_fields = ["id", "last_seen_at"]
        # The view upserts by token (a device re-registering, possibly under
        # a new user, is expected) via update_or_create - ModelSerializer's
        # auto-generated UniqueValidator would otherwise reject that exact
        # case before the view ever got a chance to reassign it.
        extra_kwargs = {"token": {"validators": []}}


class DeviceTokenUnregisterSerializer(serializers.Serializer):
    token = serializers.CharField()
