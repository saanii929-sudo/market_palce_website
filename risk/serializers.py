from rest_framework import serializers

from .models import RiskFlag


class RiskFlagSerializer(serializers.ModelSerializer):
    content_type_name = serializers.CharField(source="content_type.model", read_only=True)
    reviewed_by_name = serializers.CharField(source="reviewed_by.full_name", read_only=True, default=None)

    class Meta:
        model = RiskFlag
        fields = [
            "id",
            "content_type",
            "content_type_name",
            "object_id",
            "flag_type",
            "score",
            "details",
            "reviewed",
            "reviewed_by",
            "reviewed_by_name",
            "reviewed_at",
            "action_taken",
            "created_at",
        ]
        read_only_fields = fields


class RiskFlagReviewSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["none", "suspend_user", "void_redemption"], default="none")
