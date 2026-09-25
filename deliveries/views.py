from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Delivery, Trip
from .serializers import (
    DeliverySerializer,
    ProofOfDeliverySerializer,
    ProofOfDeliverySubmitSerializer,
    RateRiderSerializer,
    TripSerializer,
)
from .services import (
    TripError,
    build_tracking_payload,
    complete_trip,
    confirm_pickup,
    dispatch_delivery,
    rate_seller_fulfillment,
    rate_trip,
    submit_proof_of_delivery,
)


def _rider_profile_or_403(request):
    rider_profile = getattr(request.user, "rider_profile", None)
    if rider_profile is None:
        return None, Response({"detail": "You don't have a rider account."}, status=status.HTTP_403_FORBIDDEN)
    return rider_profile, None


class DeliveryDispatchView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        delivery = get_object_or_404(Delivery, pk=pk)
        offer = dispatch_delivery(delivery)
        delivery.refresh_from_db()

        if offer is None:
            return Response(DeliverySerializer(delivery).data)
        return Response(DeliverySerializer(delivery).data, status=status.HTTP_201_CREATED)


class DeliveryTrackingView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        delivery = get_object_or_404(Delivery, pk=pk)
        customer = delivery.customer_user
        is_owner = customer is not None and customer.id == request.user.id
        if not (is_owner or request.user.is_staff):
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        return Response(build_tracking_payload(delivery))


class TripConfirmPickupView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = get_object_or_404(Trip, pk=pk)
        try:
            trip = confirm_pickup(trip, rider_profile)
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(TripSerializer(trip).data)


class TripProofOfDeliveryView(APIView):
    serializer_class = ProofOfDeliverySubmitSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, pk):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = get_object_or_404(Trip, pk=pk)
        serializer = ProofOfDeliverySubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        otp_code = serializer.validated_data["otp_code"] or None
        photo = serializer.validated_data["photo"] or None

        try:
            pod = submit_proof_of_delivery(trip, rider_profile, otp_code=otp_code, photo=photo)
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ProofOfDeliverySerializer(pod).data)


class TripCompleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = get_object_or_404(Trip, pk=pk)
        try:
            trip = complete_trip(trip, rider_profile)
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(TripSerializer(trip).data)


class TripRateRiderView(APIView):
    serializer_class = RateRiderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        trip = get_object_or_404(Trip, pk=pk)
        serializer = RateRiderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            rating = rate_trip(
                trip, request.user,
                stars=serializer.validated_data["stars"], comment=serializer.validated_data["comment"],
            )
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"id": rating.id, "stars": rating.stars, "comment": rating.comment}, status=status.HTTP_201_CREATED
        )


class TripRateSellerView(APIView):
    serializer_class = RateRiderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        rider_profile, error = _rider_profile_or_403(request)
        if error:
            return error

        trip = get_object_or_404(Trip, pk=pk)
        serializer = RateRiderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            rating = rate_seller_fulfillment(
                trip, rider_profile,
                stars=serializer.validated_data["stars"], comment=serializer.validated_data["comment"],
            )
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"id": rating.id, "stars": rating.stars, "comment": rating.comment}, status=status.HTTP_201_CREATED
        )
