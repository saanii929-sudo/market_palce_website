import csv

from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from cart.models import Coupon
from orders.services.payment_gateway import HubtelGateway, PaymentGatewayError
from orders.serializers import WebhookResponseSerializer
from .models import BulkUploadJob, Payout, SellerApplication, SellerSubscription
from .serializers import (
    BulkUploadJobSerializer,
    PayoutRequestSerializer,
    PayoutSerializer,
    SellerApplicationCreateSerializer,
    SellerApplicationReviewSerializer,
    SellerApplicationSerializer,
    SellerCouponCreateSerializer,
    SellerCouponSerializer,
    SellerKYCReviewSerializer,
    SellerKYCSubmitSerializer,
)
from .services import (
    PayoutError,
    SellerApplicationError,
    approve_application,
    finalize_subscription_payment,
    get_available_balance,
    mark_subscription_failed,
    reject_application,
    reject_kyc,
    request_withdrawal,
    submit_application,
    submit_kyc,
    verify_kyc,
)
from .tasks import process_bulk_upload


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


class SellerKYCSubmitView(APIView):
    serializer_class = SellerKYCSubmitSerializer
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request):
        application = SellerApplication.objects.filter(
            user=request.user, status=SellerApplication.Status.APPROVED
        ).order_by("-submitted_at").first()
        if application is None:
            return Response({"detail": "No approved seller application found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SellerKYCSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            application = submit_kyc(application, **serializer.validated_data)
        except SellerApplicationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

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


class SellerCouponListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return SellerCouponCreateSerializer if self.request.method == "POST" else SellerCouponSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Coupon.objects.none()
        seller = getattr(self.request.user, "seller_profile", None)
        if seller is None:
            return Coupon.objects.none()
        return Coupon.objects.filter(seller=seller).order_by("-created_at")

    def post(self, request):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            return Response({"detail": "You don't have a seller account."}, status=status.HTTP_403_FORBIDDEN)

        serializer = SellerCouponCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.save(seller=seller, scope=Coupon.Scope.SELLER)
        return Response(SellerCouponSerializer(coupon).data, status=status.HTTP_201_CREATED)


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


class AdminSellerKYCQueueView(generics.ListAPIView):
    serializer_class = SellerApplicationSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SellerApplication.objects.none()

        from django.db.models import Q

        return (
            SellerApplication.objects.filter(
                status=SellerApplication.Status.APPROVED, kyc_status=SellerApplication.KYCStatus.PENDING,
            )
            .exclude(Q(bank_account_number="") & Q(momo_number=""))
            .select_related("user", "category")
            .order_by("submitted_at")
        )


class AdminSellerKYCVerifyView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=None, responses=SellerApplicationSerializer)
    def post(self, request, pk):
        application = get_object_or_404(SellerApplication, pk=pk)
        try:
            verify_kyc(application)
        except SellerApplicationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SellerApplicationSerializer(application).data)


class AdminSellerKYCRejectView(APIView):
    serializer_class = SellerKYCReviewSerializer
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        application = get_object_or_404(SellerApplication, pk=pk)
        serializer = SellerKYCReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            reject_kyc(application, reviewer_note=serializer.validated_data["reviewer_note"])
        except SellerApplicationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(SellerApplicationSerializer(application).data)


class SellerBulkUploadCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(request=OpenApiTypes.BINARY, responses=BulkUploadJobSerializer)
    def post(self, request):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            return Response({"detail": "You don't have a seller account."}, status=status.HTTP_403_FORBIDDEN)

        file = request.FILES.get("file")
        if file is None:
            return Response({"detail": "Please attach a CSV file."}, status=status.HTTP_400_BAD_REQUEST)
        if not file.name.lower().endswith(".csv"):
            return Response({"detail": "Only CSV files are supported."}, status=status.HTTP_400_BAD_REQUEST)

        job = BulkUploadJob.objects.create(seller=seller, file=file)
        transaction.on_commit(lambda: process_bulk_upload.delay(job.id))
        return Response(BulkUploadJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class SellerBulkUploadDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, job_id):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            return Response({"detail": "You don't have a seller account."}, status=status.HTTP_403_FORBIDDEN)

        job = get_object_or_404(BulkUploadJob, id=job_id, seller=seller)

        # "download" rather than "format" - DRF reserves the `format` query
        # param for its own content-negotiation override and would 406
        # before this view even ran if we used it here.
        if request.query_params.get("download") == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = f'attachment; filename="bulk-upload-{job.id}-errors.csv"'
            writer = csv.writer(response)
            writer.writerow(["row", "errors"])
            for entry in job.error_report:
                writer.writerow([entry["row"], "; ".join(entry["errors"])])
            return response

        return Response(BulkUploadJobSerializer(job).data)


class SubscriptionWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        request=OpenApiTypes.OBJECT,
        examples=[
            OpenApiExample(
                "Hubtel callback",
                description="Hubtel's actual callback shape - see HubtelGateway.parse_webhook_event.",
                value={"Data": {"ClientReference": "SUB-A1B2C3D4E5F6", "Status": "Success"}},
                request_only=True,
            ),
        ],
        responses={
            200: WebhookResponseSerializer,
            404: WebhookResponseSerializer,
        },
        description=(
            "Hubtel's callback body isn't cryptographically signed, so this always re-confirms "
            "the result against Hubtel's own status API (HubtelGateway.check_status) before "
            "activating the subscription, rather than trusting the callback payload directly."
        ),
    )
    def post(self, request):
        

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
