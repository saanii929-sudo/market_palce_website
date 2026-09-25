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
from .services import ParcelError, cancel_parcel, create_parcel, get_delivery_for_parcel, get_parcel_quote


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


class ParcelCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        parcel = get_object_or_404(Parcel, pk=pk)
        try:
            parcel = cancel_parcel(parcel, request.user)
        except ParcelError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ParcelSerializer(parcel).data)
