from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Review
from .serializers import ReviewCreateSerializer, ReviewSerializer, ReviewUpdateSerializer


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
