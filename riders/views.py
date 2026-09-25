from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RiderDocument, RiderPayoutAccount
from .serializers import (
    ActiveDeliverySerializer,
    CashOutSerializer,
    DeliveryCompleteSerializer,
    DeliveryHistorySerializer,
    EarningsActivitySerializer,
    LocationPingSerializer,
    NewEarningsSummarySerializer,
    PayoutMethodCreateSerializer,
    PayoutMethodSerializer,
    RiderDocumentReviewSerializer,
    RiderDocumentSerializer,
    RiderDocumentUploadSerializer,
    RiderEarningSerializer,
    RiderOnlineToggleSerializer,
    RiderPayoutRequestSerializer,
    RiderPayoutSerializer,
    RiderProfileSerializer,
    RiderRegisterSerializer,
    RiderReviewSerializer,
    RiderReviewsSummarySerializer,
    RiderSettingsSerializer,
    VehicleSerializer,
    VehicleUpdateSerializer,
    VerificationStatusSerializer,
)
from .services import (
    RiderError,
    add_payout_method,
    current_vehicle,
    get_deliveries_history,
    get_earnings_activity,
    get_earnings_summary,
    get_earnings_transactions,
    get_reviews,
    get_reviews_summary,
    list_payout_methods,
    register_rider,
    request_payout,
    review_document,
    set_default_payout_method,
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
    """GET lists the rider's own documents (the same RiderDocument rows
    GET /riders/verification-status/ derives its per-document status from -
    one source of truth for both). POST uploads/replaces one document."""

    serializer_class = RiderDocumentUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error
        documents = rider_profile.documents.all()
        return Response(RiderDocumentSerializer(documents, many=True, context={"request": request}).data)

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

    # PATCH /riders/status/ is the same operation under the path/verb the
    # rider app's status screen expects - POST /riders/online/ keeps working
    # unchanged for anything already wired to it.
    patch = post


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

        summary = get_earnings_summary(rider_profile)
        return Response(NewEarningsSummarySerializer(summary).data)


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


class RiderDeliveryRequestAcceptView(APIView):
    """POST /riders/delivery-requests/{id}/accept/ - same operation as
    RiderOfferAcceptView (both call deliveries.services.accept_offer), just
    returns the shared ActiveDeliverySerializer shape (see #3 of the brief)
    instead of TripSerializer, and never includes the delivery code."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.models import DeliveryOffer
        from deliveries.services import DispatchError, accept_offer, build_active_delivery_payload

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        offer = get_object_or_404(DeliveryOffer, pk=pk)
        try:
            trip = accept_offer(offer, rider_profile)
        except DispatchError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            ActiveDeliverySerializer(build_active_delivery_payload(trip)).data, status=status.HTTP_201_CREATED
        )


class RiderDeliveryRequestDeclineView(APIView):
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

        return Response(status=status.HTTP_200_OK)


class RiderActiveDeliveryView(APIView):
    """GET /riders/deliveries/active/ - lets the app rehydrate this screen
    after a restart. 204 (no body) if the rider has nothing in progress."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from deliveries.models import Trip
        from deliveries.services import build_active_delivery_payload

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = (
            Trip.objects.filter(rider=rider_profile, status__in=Trip.ACTIVE_STATUSES)
            .select_related("delivery")
            .order_by("-created_at")
            .first()
        )
        if trip is None:
            return Response(status=status.HTTP_204_NO_CONTENT)

        return Response(ActiveDeliverySerializer(build_active_delivery_payload(trip)).data)


class RiderDeliveryConfirmPickupView(APIView):
    """POST /riders/deliveries/{id}/confirm-pickup/ - {id} is the Trip id,
    the same id GET /riders/deliveries/active/ and the accept response
    return. Same underlying deliveries.services.confirm_pickup as
    TripConfirmPickupView, reshaped to ActiveDeliverySerializer."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.models import Trip
        from deliveries.services import TripError, build_active_delivery_payload, confirm_pickup

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = get_object_or_404(Trip, pk=pk)
        try:
            trip = confirm_pickup(trip, rider_profile)
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ActiveDeliverySerializer(build_active_delivery_payload(trip)).data)


class RiderDeliveryCompleteView(APIView):
    """POST /riders/deliveries/{id}/complete/ - body: {"delivery_code": "1234"}.
    Validated server-side against the code generated at accept-time
    (deliveries.services.complete_delivery_with_code); a wrong code is
    rejected with a plain 400, matching how every other business-rule
    error in this app responds (as opposed to a DRF field-validation error,
    which already goes through the shared exception handler automatically)."""

    serializer_class = DeliveryCompleteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.models import Trip
        from deliveries.services import TripError, build_active_delivery_payload, complete_delivery_with_code

        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = DeliveryCompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        trip = get_object_or_404(Trip, pk=pk)
        try:
            trip = complete_delivery_with_code(
                trip, rider_profile, delivery_code=serializer.validated_data["delivery_code"]
            )
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ActiveDeliverySerializer(build_active_delivery_payload(trip)).data)


class RiderDeliveriesHistoryView(generics.ListAPIView):
    serializer_class = DeliveryHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return []

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            return []
        return get_deliveries_history(rider_profile)


class RiderEarningsActivityView(generics.ListAPIView):
    serializer_class = EarningsActivitySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return []

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            return []
        return get_earnings_activity(rider_profile)


class RiderPayoutMethodListCreateView(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return PayoutMethodCreateSerializer if self.request.method == "POST" else PayoutMethodSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RiderPayoutAccount.objects.none()

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            return RiderPayoutAccount.objects.none()
        return list_payout_methods(rider_profile)

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        data = [PayoutMethodSerializer.build(account) for account in (page if page is not None else queryset)]
        if page is not None:
            return self.get_paginated_response(data)
        return Response(data)

    def post(self, request, *args, **kwargs):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = PayoutMethodCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            account = add_payout_method(
                rider_profile,
                provider=serializer.validated_data["provider"],
                account_number=serializer.validated_data["account_number"],
                type=serializer.validated_data["type"],
            )
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PayoutMethodSerializer.build(account), status=status.HTTP_201_CREATED)


class RiderPayoutMethodSetDefaultView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        try:
            account = set_default_payout_method(rider_profile, pk)
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PayoutMethodSerializer.build(account))


class RiderCashOutView(APIView):
    serializer_class = CashOutSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        serializer = CashOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            payout = request_payout(
                rider_profile,
                amount=serializer.validated_data["amount"],
                payout_account=serializer.validated_data["payout_method_id"],
            )
        except RiderError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        rider_profile.refresh_from_db()
        from .services import get_available_earnings_balance

        return Response({
            "available_balance": get_available_earnings_balance(rider_profile),
            "payout": RiderPayoutSerializer(payout).data,
        }, status=status.HTTP_201_CREATED)


class RiderReviewsListView(generics.ListAPIView):
    serializer_class = RiderReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from .models import RiderRating

            return RiderRating.objects.none()

        rider_profile = getattr(self.request.user, "rider_profile", None)
        if rider_profile is None:
            from .models import RiderRating

            return RiderRating.objects.none()
        return get_reviews(rider_profile)


class RiderReviewsSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        return Response(RiderReviewsSummarySerializer(get_reviews_summary(rider_profile)).data)


