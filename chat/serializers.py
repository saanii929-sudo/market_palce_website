from rest_framework import serializers

from catalog.models import Seller

from .models import Conversation, Message


class ChatUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()


class ChatSellerSerializer(serializers.ModelSerializer):
    logo = serializers.CharField(source="resolved_logo_url", read_only=True, allow_null=True)

    class Meta:
        model = Seller
        fields = ["id", "business_name", "slug", "logo"]


class MessageSerializer(serializers.ModelSerializer):
    sender_id = serializers.IntegerField(source="sender.id", read_only=True)
    sender_name = serializers.CharField(source="sender.full_name", read_only=True)

    class Meta:
        model = Message
        fields = ["id", "conversation_id", "sender_id", "sender_name", "body", "created_at"]
        read_only_fields = fields


class MessageCreateSerializer(serializers.Serializer):
    body = serializers.CharField(max_length=4000)


class ConversationSerializer(serializers.ModelSerializer):
    seller = ChatSellerSerializer(read_only=True)
    customer = ChatUserSerializer(read_only=True)
    last_message = serializers.SerializerMethodField()
    unread_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id", "kind", "customer", "seller", "last_message_at", "last_message", "unread_count", "created_at",
        ]

    def get_last_message(self, obj) -> dict | None:
        last = obj.messages.order_by("-created_at").first()
        return MessageSerializer(last).data if last else None

    def get_unread_count(self, obj) -> int:
        from .services import unread_count

        request = self.context.get("request")
        if request is None:
            return 0
        return unread_count(obj, request.user)


class StartConversationSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=Conversation.Kind.choices)
    seller_slug = serializers.SlugField(required=False, allow_blank=True)

    def validate(self, attrs):
        if attrs["kind"] == Conversation.Kind.CUSTOMER_SELLER and not attrs.get("seller_slug"):
            raise serializers.ValidationError({"seller_slug": "Required for customer_seller conversations."})
        return attrs
