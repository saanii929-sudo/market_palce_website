from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RiderDocument
from .serializers import (
    EarningsSummarySerializer,
    LocationPingSerializer,
    RiderDocumentReviewSerializer,
    RiderDocumentSerializer,
    RiderDocumentUploadSerializer,
    RiderEarningSerializer,
    RiderOnlineToggleSerializer,
    RiderPayoutRequestSerializer,
    RiderPayoutSerializer,
    RiderProfileSerializer,
    RiderRegisterSerializer,
    RiderSettingsSerializer,
    VehicleSerializer,
    VehicleUpdateSerializer,
    VerificationStatusSerializer,
)
from .services import (
    RiderError,
    current_vehicle,
    get_earnings_summary,
    get_earnings_transactions,
    register_rider,
    request_payout,
    review_document,
    set_online,
    update_rider_settings,
    upload_document,
    upsert_vehicle,
)


def _rider_profile_or_403(request):
    rider_profile = getattr(request.user, "rider_profile", None)
    if rider_profile is None:
        return None, Response({"detail": "You don't have a rider account."}, status=status.HTTP_403_FORBIDDEN)
    return rider_profile, None


class RiderRegisterView(APIView):
    serializer_class = RiderRegisterSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from accounts.models import OTPCode
        from accounts.serializers import UserSerializer
        from accounts.services.otp import send_otp

        serializer = RiderRegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            with transaction.atomic():
                rider_profile = register_rider(**serializer.validated_data)
                destination = rider_profile.user.email or rider_profile.user.phone
                send_otp(destination, OTPCode.Purpose.SIGNUP_VERIFY, user=rider_profile.user)
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {
                "detail": "Account created. Please verify your account with the code we sent.",
                "user": UserSerializer(rider_profile.user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class RiderDocumentUploadView(APIView):
    serializer_class = RiderDocumentUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = RiderDocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        document = upload_document(
            rider_profile,
            doc_type=serializer.validated_data["doc_type"],
            file=serializer.validated_data["file"],
            expires_at=serializer.validated_data["expires_at"],
        )
        return Response(RiderDocumentSerializer(document).data, status=status.HTTP_201_CREATED)


class RiderVerificationStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error
        return Response(VerificationStatusSerializer.build(rider_profile))


class RiderVehicleView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        vehicle = current_vehicle(rider_profile)
        if vehicle is None:
            return Response({"detail": "No vehicle on file."}, status=status.HTTP_404_NOT_FOUND)
        return Response(VehicleSerializer(vehicle).data)

    def patch(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = VehicleUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        vehicle = upsert_vehicle(rider_profile, **serializer.validated_data)
        return Response(VehicleSerializer(vehicle).data)


class RiderSettingsView(APIView):
    serializer_class = RiderSettingsSerializer
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = RiderSettingsSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        update_rider_settings(rider_profile, serializer.validated_data)
        rider_profile.refresh_from_db()
        return Response(RiderProfileSerializer(rider_profile).data)


class AdminRiderVerificationQueueView(generics.ListAPIView):
    serializer_class = RiderDocumentSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RiderDocument.objects.none()
        return (
            RiderDocument.objects.filter(status=RiderDocument.Status.PENDING)
            .select_related("rider__user")
            .order_by("created_at")
        )


class AdminRiderDocumentReviewView(APIView):
    serializer_class = RiderDocumentReviewSerializer
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        document = get_object_or_404(RiderDocument, pk=pk)
        serializer = RiderDocumentReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review_document(
                document, action=serializer.validated_data["action"],
                reviewer_note=serializer.validated_data["reviewer_note"],
            )
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RiderDocumentSerializer(document).data)


class RiderOnlineToggleView(APIView):
    serializer_class = RiderOnlineToggleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = RiderOnlineToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            set_online(rider_profile, serializer.validated_data["is_online"])
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RiderProfileSerializer(rider_profile).data)


class RiderLocationPingView(APIView):
    serializer_class = LocationPingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = LocationPingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rider_profile.update_location(serializer.validated_data["lat"], serializer.validated_data["lng"])
        return Response({"detail": "Location updated."})


class RiderPendingOffersView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        from deliveries.serializers import DeliveryOfferSerializer

        return DeliveryOfferSerializer

    def get_queryset(self):
        from deliveries.models import DeliveryOffer
        from deliveries.services import get_pending_offers_for_rider

        if getattr(self, "swagger_fake_view", False):
            return DeliveryOffer.objects.none()

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            return DeliveryOffer.objects.none()
        return get_pending_offers_for_rider(rider_profile)


class RiderOfferAcceptView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.models import DeliveryOffer
        from deliveries.serializers import TripSerializer
        from deliveries.services import DispatchError, accept_offer

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        offer = get_object_or_404(DeliveryOffer, pk=pk)
        try:
            trip = accept_offer(offer, rider_profile)
        except DispatchError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(TripSerializer(trip).data, status=status.HTTP_201_CREATED)


class RiderEarningsSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        summary = get_earnings_summary(rider_profile, period=request.query_params.get("period", "today"))
        return Response(EarningsSummarySerializer(summary).data)


class RiderEarningsTransactionsView(generics.ListAPIView):
    serializer_class = RiderEarningSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from .models import RiderEarning

            return RiderEarning.objects.none()

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            from .models import RiderEarning

            return RiderEarning.objects.none()
        return get_earnings_transactions(rider_profile)


class RiderPayoutRequestView(APIView):
    serializer_class = RiderPayoutRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = RiderPayoutRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payout = request_payout(
                rider_profile,
                amount=serializer.validated_data["amount"],
                payout_account=serializer.validated_data["payout_account_id"],
            )
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RiderPayoutSerializer(payout).data, status=status.HTTP_201_CREATED)


class RiderOfferDeclineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.models import DeliveryOffer
        from deliveries.services import DispatchError, decline_offer

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        offer = get_object_or_404(DeliveryOffer, pk=pk)
        try:
            decline_offer(offer, rider_profile)
        except DispatchError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"detail": "Offer declined."})
