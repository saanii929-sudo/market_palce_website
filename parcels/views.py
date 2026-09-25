from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Parcel
from .serializers import (
    ParcelCreateSerializer,
    ParcelQuoteResponseSerializer,
    ParcelQuoteSerializer,
    ParcelSerializer,
)
from .services import (
    ParcelError,
    cancel_parcel,
    check_and_finalize_parcel_payment,
    create_parcel,
    find_rider_for_parcel,
    get_delivery_for_parcel,
    get_parcel_quote,
    initiate_parcel_checkout,
)


class ParcelQuoteView(APIView):
    serializer_class = ParcelQuoteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ParcelQuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        quote = get_parcel_quote(**serializer.validated_data)
        return Response(ParcelQuoteResponseSerializer(quote).data)


class ParcelListCreateView(generics.ListCreateAPIView):
    serializer_class = ParcelSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Parcel.objects.none()
        return Parcel.objects.filter(sender=self.request.user)

    def create(self, request, *args, **kwargs):
        serializer = ParcelCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)

        data = dict(serializer.validated_data)
        data["pickup_address"] = data.pop("pickup_address_id")

        try:
            parcel = create_parcel(request.user, **data)
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ParcelSerializer(parcel).data, status=status.HTTP_201_CREATED)


class ParcelCheckoutView(APIView):
    """POST /parcels/{id}/checkout/ - starts a Hubtel checkout for the
    parcel's price. The client should open the returned checkout_url, then
    poll ParcelCheckoutStatusView (or wait for find-rider to unblock once
    the webhook lands)."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from django.conf import settings
        from django.urls import reverse

        parcel = get_object_or_404(Parcel, pk=pk)
        try:
            parcel = initiate_parcel_checkout(
                parcel, request.user,
                callback_url=request.build_absolute_uri(reverse("parcel-payment-webhook")),
                return_url=request.data.get("return_url") or settings.HUBTEL_RETURN_URL,
                cancellation_url=request.data.get("cancellation_url") or settings.HUBTEL_CANCELLATION_URL,
            )
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ParcelSerializer(parcel).data)


class ParcelCheckoutStatusView(APIView):
    """GET /parcels/{id}/checkout/status/ - the client polls this after
    opening checkout_url; it re-confirms against Hubtel directly rather than
    waiting on the webhook."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk, sender=request.user)
        try:
            parcel = check_and_finalize_parcel_payment(parcel)
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_502_BAD_GATEWAY)

        return Response(ParcelSerializer(parcel).data)


class ParcelPaymentWebhookView(APIView):
    """Hubtel's callback for parcel payments - a parcel isn't a
    PendingCheckout/Order so this is separate from orders' generic
    /payments/webhook/<gateway>/ endpoint. Same rule as that one: the
    callback body isn't trusted directly, always re-confirmed via
    HubtelGateway.check_status first."""

    permission_classes = [permissions.AllowAny]

    def post(self, request):
        from orders.services.payment_gateway import HubtelGateway

        event = HubtelGateway().parse_webhook_event(request)
        parcel = Parcel.objects.filter(payment_reference=event.get("reference")).first()
        if parcel is None:
            return Response({"detail": "Unknown reference."}, status=status.HTTP_404_NOT_FOUND)

        try:
            check_and_finalize_parcel_payment(parcel)
        except ParcelError:
            pass

        return Response({"detail": "Webhook processed."})


class ParcelFindRiderView(APIView):
    """The explicit 'search for a rider' step - POST /parcels/ only creates
    the package (see parcels.services.create_parcel); this is what actually
    starts matching, so the client can show its own 'Finding your rider...'
    screen and retry on 'no riders available' instead of dispatch being an
    invisible side effect of creation."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk)
        try:
            payload = find_rider_for_parcel(parcel, request.user)
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(payload)


class ParcelTrackingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        from deliveries.services import build_tracking_payload

        parcel = get_object_or_404(Parcel, pk=pk)
        delivery = get_delivery_for_parcel(parcel)
        is_owner = parcel.sender_id == request.user.id
        if delivery is None or not (is_owner or request.user.is_staff):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(build_tracking_payload(delivery))


class ParcelRateRiderView(APIView):
    """Same pattern as orders.views.SellerOrderRateRiderView - resolves
    parcel -> Delivery -> Trip and delegates to the one existing rate_trip."""

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        from deliveries.services import TripError, rate_trip
        from riders.serializers import RateRiderSubmitSerializer

        parcel = get_object_or_404(Parcel, pk=pk, sender=request.user)
        serializer = RateRiderSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delivery = get_delivery_for_parcel(parcel)
        trip = getattr(delivery, "trip", None) if delivery else None
        if trip is None:
            return Response({"detail": "No rider trip found for this package."}, status=status.HTTP_404_NOT_FOUND)

        try:
            rating = rate_trip(
                trip, request.user,
                stars=serializer.validated_data["rating"], comment=serializer.validated_data["comment"],
            )
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"id": rating.id, "rating": rating.stars, "comment": rating.comment}, status=status.HTTP_201_CREATED
        )


class ParcelCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk)
        try:
            parcel = cancel_parcel(parcel, request.user)
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ParcelSerializer(parcel).data)
