from django.db.models import Q
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from orders.models import Order

from .models import Dispute
from .serializers import (
    DisputeCreateSerializer,
    DisputeMessageCreateSerializer,
    DisputeMessageSerializer,
    DisputeResolveSerializer,
    DisputeSerializer,
)
from .services import DisputeError, add_message, create_dispute, resolve_dispute


class DisputeListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return DisputeCreateSerializer if self.request.method == "POST" else DisputeSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Dispute.objects.none()

        user = self.request.user
        qs = Dispute.objects.select_related(
            "order", "refund_request", "raised_by", "against", "assigned_admin"
        ).prefetch_related("messages")

        if user.is_staff or user.is_superuser:
            status_param = self.request.query_params.get("status")
            category_param = self.request.query_params.get("category")
            if status_param:
                qs = qs.filter(status=status_param)
            if category_param:
                qs = qs.filter(category=category_param)
            return qs

        seller = getattr(user, "seller_profile", None)
        if seller is not None:
            return qs.filter(Q(raised_by=user) | Q(order__seller_orders__seller=seller)).distinct()

        return qs.filter(raised_by=user)

    @extend_schema(request=DisputeCreateSerializer, responses=DisputeSerializer)
    def post(self, request):
        serializer = DisputeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        order = get_object_or_404(Order, order_number=serializer.validated_data["order_number"])

        seller_order = None
        seller_order_id = serializer.validated_data["seller_order_id"]
        if seller_order_id:
            seller_order = order.seller_orders.filter(id=seller_order_id).first()
        elif order.seller_orders.count() == 1:
            seller_order = order.seller_orders.first()

        against = None
        if seller_order is not None:
            seller = getattr(request.user, "seller_profile", None)
            if seller is not None and seller_order.seller_id == seller.id:
                against = order.user
            else:
                against = seller_order.seller.user

        try:
            dispute = create_dispute(
                order=order,
                raised_by=request.user,
                category=serializer.validated_data["category"],
                description=serializer.validated_data["description"],
                evidence=serializer.validated_data["evidence"],
                against=against,
            )
        except DisputeError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(DisputeSerializer(dispute).data, status=status.HTTP_201_CREATED)


class DisputeMessageCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=DisputeMessageCreateSerializer, responses=DisputeMessageSerializer)
    def post(self, request, dispute_id):
        dispute = get_object_or_404(Dispute, id=dispute_id)

        serializer = DisputeMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            message = add_message(
                dispute=dispute,
                sender=request.user,
                message=serializer.validated_data["message"],
                attachments=serializer.validated_data["attachments"],
            )
        except DisputeError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(DisputeMessageSerializer(message).data, status=status.HTTP_201_CREATED)


class DisputeResolveView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=DisputeResolveSerializer, responses=DisputeSerializer)
    def post(self, request, dispute_id):
        dispute = get_object_or_404(Dispute, id=dispute_id)

        serializer = DisputeResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            dispute = resolve_dispute(
                dispute=dispute,
                admin_user=request.user,
                new_status=serializer.validated_data["status"],
                resolution_note=serializer.validated_data["resolution_note"],
            )
        except DisputeError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(DisputeSerializer(dispute).data)
