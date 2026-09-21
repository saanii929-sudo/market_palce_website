from rest_framework import serializers

from .models import Dispute, DisputeMessage


class DisputeMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = DisputeMessage
        fields = ["id", "sender", "sender_name", "message", "attachments", "created_at"]


class DisputeMessageCreateSerializer(serializers.Serializer):
    message = serializers.CharField(required=False, allow_blank=True, default="")
    attachments = serializers.ListField(child=serializers.URLField(), required=False, default=list)


class DisputeSerializer(serializers.ModelSerializer):
    order_number = serializers.CharField(source="order.order_number", read_only=True)
    raised_by_name = serializers.CharField(source="raised_by.full_name", read_only=True)
    against_name = serializers.CharField(source="against.full_name", read_only=True, default=None)
    assigned_admin_name = serializers.CharField(source="assigned_admin.full_name", read_only=True, default=None)
    messages = DisputeMessageSerializer(many=True, read_only=True)

    class Meta:
        model = Dispute
        fields = [
            "id",
            "order",
            "order_number",
            "refund_request",
            "raised_by",
            "raised_by_name",
            "against",
            "against_name",
            "category",
            "description",
            "evidence",
            "status",
            "assigned_admin",
            "assigned_admin_name",
            "resolution_note",
            "resolved_at",
            "created_at",
            "messages",
        ]


class DisputeCreateSerializer(serializers.Serializer):
    order_number = serializers.CharField()
    seller_order_id = serializers.IntegerField(required=False, allow_null=True, default=None)
    category = serializers.ChoiceField(choices=Dispute.Category.choices)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    evidence = serializers.ListField(child=serializers.URLField(), required=False, default=list)


class DisputeResolveSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[
            Dispute.Status.RESOLVED_BUYER_FAVOR,
            Dispute.Status.RESOLVED_SELLER_FAVOR,
            Dispute.Status.RESOLVED_PARTIAL,
        ]
    )
    resolution_note = serializers.CharField(required=False, allow_blank=True, default="")
