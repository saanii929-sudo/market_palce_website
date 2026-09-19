from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Seller

from .models import Conversation
from .serializers import (
    ConversationSerializer,
    MessageCreateSerializer,
    MessageSerializer,
    StartConversationSerializer,
)
from .services import (
    ChatError,
    mark_read,
    post_message,
    start_customer_seller_conversation,
    start_customer_support_conversation,
    start_seller_support_conversation,
    user_can_access_conversation,
)


class ConversationListView(generics.ListAPIView):
    """The caller's own conversations: as a customer (customer_seller and
    customer_support threads), as a seller (customer_seller and
    seller_support threads for their store), or - for staff - every support
    thread across the platform (the shared support inbox)."""

    serializer_class = ConversationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Conversation.objects.none()

        user = self.request.user
        if user.is_staff or user.is_superuser:
            return Conversation.objects.filter(
                kind__in=[Conversation.Kind.CUSTOMER_SUPPORT, Conversation.Kind.SELLER_SUPPORT]
            ).select_related("customer", "seller")

        seller = getattr(user, "seller_profile", None)
        query = Q(customer=user)
        if seller is not None:
            query |= Q(seller=seller)
        return Conversation.objects.filter(query).select_related("customer", "seller")


class ConversationStartView(APIView):
    """Gets-or-creates a conversation and returns it - safe to call every
    time the "Message seller"/"Contact support" button is pressed."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=StartConversationSerializer, responses=ConversationSerializer)
    def post(self, request):
        serializer = StartConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        kind = serializer.validated_data["kind"]

        try:
            if kind == Conversation.Kind.CUSTOMER_SELLER:
                seller = get_object_or_404(Seller, slug=serializer.validated_data["seller_slug"])
                conversation = start_customer_seller_conversation(request.user, seller)
            elif kind == Conversation.Kind.CUSTOMER_SUPPORT:
                conversation = start_customer_support_conversation(request.user)
            else:
                conversation = start_seller_support_conversation(request.user)
        except ChatError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ConversationSerializer(conversation, context={"request": request}).data)


class ConversationMessageListCreateView(generics.ListCreateAPIView):
    """GET lists message history (oldest first, paginated); POST sends a new
    message - persisted here and broadcast live over the conversation's
    WebSocket group, so REST and WebSocket clients of the same conversation
    always see the same messages."""

    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return MessageCreateSerializer if self.request.method == "POST" else MessageSerializer

    def get_conversation(self):
        conversation = get_object_or_404(Conversation, id=self.kwargs["conversation_id"])
        if not user_can_access_conversation(self.request.user, conversation):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You don't have access to this conversation.")
        return conversation

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from .models import Message

            return Message.objects.none()
        return self.get_conversation().messages.select_related("sender")

    @extend_schema(request=MessageCreateSerializer, responses=MessageSerializer)
    def post(self, request, *args, **kwargs):
        conversation = self.get_conversation()
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            message = post_message(conversation, request.user, serializer.validated_data["body"])
        except ChatError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)


class ConversationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=None)
    def post(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id)
        if not user_can_access_conversation(request.user, conversation):
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied("You don't have access to this conversation.")

        mark_read(conversation, request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
