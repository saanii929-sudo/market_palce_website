from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Review
from .serializers import (
    AdminReviewSerializer,
    ReviewCreateSerializer,
    ReviewFlagCreateSerializer,
    ReviewModerateSerializer,
    ReviewSerializer,
    ReviewUpdateSerializer,
)
from .services import ReviewModerationError, flag_review, moderate_review


class ReviewCreateView(APIView):
    serializer_class = ReviewCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = ReviewCreateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        review = serializer.save()
        return Response(ReviewSerializer(review).data, status=201)


class IsReviewOwner(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return obj.user_id == request.user.id


class ReviewUpdateDeleteView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Review.objects.all()
    permission_classes = [permissions.IsAuthenticated, IsReviewOwner]

    def get_serializer_class(self):
        return ReviewSerializer if self.request.method == "GET" else ReviewUpdateSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        return Review.objects.all()

    def update(self, request, *args, **kwargs):
        response = super().update(request, *args, **kwargs)
        response.data = ReviewSerializer(self.get_object()).data
        return response


class ReviewFlagView(APIView):
    serializer_class = ReviewFlagCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        review = get_object_or_404(Review, pk=pk)
        serializer = ReviewFlagCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            flag_review(review=review, flagged_by=request.user, reason=serializer.validated_data["reason"])
        except ReviewModerationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response({"detail": "Review flagged."}, status=status.HTTP_201_CREATED)


class AdminFlaggedReviewListView(generics.ListAPIView):
    serializer_class = AdminReviewSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        return (
            Review.objects.filter(status=Review.Status.FLAGGED)
            .select_related("user", "product")
            .order_by("-flagged_count", "-created_at")
        )


class AdminReviewModerateView(APIView):
    serializer_class = ReviewModerateSerializer
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, pk):
        review = get_object_or_404(Review, pk=pk)
        serializer = ReviewModerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review = moderate_review(
                review=review,
                action=serializer.validated_data["action"],
                moderation_note=serializer.validated_data["moderation_note"],
            )
        except ReviewModerationError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(AdminReviewSerializer(review).data)
