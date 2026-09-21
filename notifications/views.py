from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Broadcast, DeviceToken, Notification
from .serializers import (
    BroadcastCreateSerializer,
    BroadcastSerializer,
    DeviceTokenSerializer,
    DeviceTokenUnregisterSerializer,
    NotificationSerializer,
)
from .tasks import send_broadcast


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Notification.objects.none()

        qs = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "true":
            qs = qs.filter(is_read=False)
        return qs


class NotificationMarkReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=NotificationSerializer)
    def post(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        notification.is_read = True
        notification.save(update_fields=["is_read"])
        return Response(NotificationSerializer(notification).data)


class NotificationMarkAllReadView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=None)
    def post(self, request):
        updated = Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({"marked_read": updated})


class DeviceTokenView(APIView):
    """Registers/refreshes the calling user's FCM token (call on login and
    whenever Firebase's SDK reports a token refresh), or deactivates one
    (call on logout, so a shared/reset device stops receiving pushes meant
    for whoever was signed in before)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=DeviceTokenSerializer, responses=DeviceTokenSerializer)
    def post(self, request):
        serializer = DeviceTokenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        device, _ = DeviceToken.objects.update_or_create(
            token=serializer.validated_data["token"],
            defaults={
                "user": request.user,
                "platform": serializer.validated_data["platform"],
                "is_active": True,
                "last_seen_at": timezone.now(),
            },
        )
        return Response(DeviceTokenSerializer(device).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DeviceTokenUnregisterSerializer, responses=None)
    def delete(self, request):
        serializer = DeviceTokenUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DeviceToken.objects.filter(token=serializer.validated_data["token"], user=request.user).update(
            is_active=False
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminBroadcastListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAdminUser]

    def get_serializer_class(self):
        return BroadcastCreateSerializer if self.request.method == "POST" else BroadcastSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Broadcast.objects.none()
        return Broadcast.objects.select_related("created_by").order_by("-created_at")

    @extend_schema(request=BroadcastCreateSerializer, responses=BroadcastSerializer)
    def post(self, request):
        serializer = BroadcastCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        broadcast = Broadcast.objects.create(
            title=serializer.validated_data["title"],
            body=serializer.validated_data["body"],
            audience=serializer.validated_data["audience"],
            scheduled_for=serializer.validated_data["scheduled_for"],
            created_by=request.user,
        )
        if broadcast.scheduled_for is None:
            transaction.on_commit(lambda: send_broadcast.delay(broadcast.id))
            # In eager-task dev/test setups the send above already ran and
            # updated sent_at in the DB - refresh so the response reflects
            # that instead of this now-stale pre-send snapshot. In a real
            # async setup this is a no-op (the task hasn't run yet).
            broadcast.refresh_from_db()

        return Response(BroadcastSerializer(broadcast).data, status=status.HTTP_201_CREATED)
