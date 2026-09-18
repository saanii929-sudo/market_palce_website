from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Payout, SellerApplication, SellerSubscription
from .serializers import (
    PayoutRequestSerializer,
    PayoutSerializer,
    SellerApplicationCreateSerializer,
    SellerApplicationReviewSerializer,
    SellerApplicationSerializer,
)
from .services import (
    PayoutError,
    SellerApplicationError,
    approve_application,
    finalize_subscription_payment,
    get_available_balance,
    mark_subscription_failed,
    reject_application,
    request_withdrawal,
    submit_application,
)


class SellerApplyView(APIView):
    serializer_class = SellerApplicationCreateSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = SellerApplicationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            application = submit_application(
                request.user,
                business_name=serializer.validated_data["business_name"],
                category=serializer.validated_data["category_id"],
                phone=serializer.validated_data["phone"],
                id_document=serializer.validated_data["id_document"],
                business_certificate=serializer.validated_data.get("business_certificate"),
            )
        except SellerApplicationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SellerApplicationSerializer(application).data, status=status.HTTP_201_CREATED)


class SellerApplicationStatusView(APIView):
    serializer_class = SellerApplicationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        application = SellerApplication.objects.filter(user=request.user).order_by("-submitted_at").first()
        if application is None:
            return Response({"detail": "No application on file."}, status=status.HTTP_404_NOT_FOUND)
        return Response(SellerApplicationSerializer(application).data)


class SellerPayoutListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return PayoutRequestSerializer if self.request.method == "POST" else PayoutSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Payout.objects.none()
        seller = getattr(self.request.user, "seller_profile", None)
        if seller is None:
            return Payout.objects.none()
        return Payout.objects.filter(seller=seller)

    def post(self, request):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            return Response({"detail": "You don't have a seller account."}, status=status.HTTP_403_FORBIDDEN)

        serializer = PayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payout = request_withdrawal(seller, **serializer.validated_data)
        except PayoutError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PayoutSerializer(payout).data, status=status.HTTP_201_CREATED)


class SellerBalanceView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            return Response({"detail": "You don't have a seller account."}, status=status.HTTP_403_FORBIDDEN)
        return Response({"available_balance": get_available_balance(seller)})


class AdminSellerApplicationListView(generics.ListAPIView):
    serializer_class = SellerApplicationSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SellerApplication.objects.none()

        qs = SellerApplication.objects.select_related("user", "category")
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs


class AdminSellerApplicationReviewView(APIView):
    serializer_class = SellerApplicationReviewSerializer
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        application = get_object_or_404(SellerApplication, pk=pk)
        serializer = SellerApplicationReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            if serializer.validated_data["action"] == "approve":
                approve_application(application, serializer.validated_data["reviewer_note"])
            else:
                reject_application(application, serializer.validated_data["reviewer_note"])
        except SellerApplicationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(SellerApplicationSerializer(application).data)


class SubscriptionWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from orders.services.payment_gateway import HubtelGateway, PaymentGatewayError

        reference = (request.data.get("Data") or request.data).get("ClientReference") or request.data.get(
            "clientReference"
        )
        subscription = SellerSubscription.objects.filter(reference=reference).first()
        if subscription is None:
            return Response({"detail": "Unknown reference."}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = HubtelGateway().check_status(reference)
        except PaymentGatewayError:
            result = {"status": "pending"}

        if result["status"] == "success":
            finalize_subscription_payment(subscription)
        elif result["status"] == "failed":
            mark_subscription_failed(subscription, "Payment failed or was cancelled.")

        return Response({"detail": "Webhook processed."})
