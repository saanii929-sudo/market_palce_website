from rest_framework import generics, permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import WishlistItemSerializer, WishlistToggleSerializer


class WishlistListView(generics.ListAPIView):
    serializer_class = WishlistItemSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            from .models import WishlistItem

            return WishlistItem.objects.none()
        return services.list_items(self.request)


class WishlistToggleView(APIView):
    serializer_class = WishlistToggleSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = WishlistToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        wishlisted = services.toggle(request, serializer.validated_data["product_id"])
        return Response({"wishlisted": wishlisted})
