from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import SellerApplication
from .serializers import (
    SellerApplicationCreateSerializer,
    SellerApplicationReviewSerializer,
    SellerApplicationSerializer,
)
from .services import SellerApplicationError, approve_application, reject_application, submit_application


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
