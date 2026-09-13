from rest_framework import generics, permissions

from .models import StaticPage
from .serializers import StaticPageSerializer


class StaticPageDetailView(generics.RetrieveAPIView):
    queryset = StaticPage.objects.all()
    serializer_class = StaticPageSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"
