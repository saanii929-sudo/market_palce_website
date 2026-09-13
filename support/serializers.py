from rest_framework import serializers

from .models import FAQ, SupportTicket


class FAQSerializer(serializers.ModelSerializer):
    class Meta:
        model = FAQ
        fields = ["id", "question", "answer", "topic", "display_order"]


class SupportTicketCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = SupportTicket
        fields = ["id", "channel", "subject", "message", "status", "created_at"]
        read_only_fields = ["id", "status", "created_at"]
