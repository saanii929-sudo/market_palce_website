from django.db.models import Q
from rest_framework import generics, permissions

from .models import FAQ
from .serializers import FAQSerializer, SupportTicketCreateSerializer


class FAQListView(generics.ListAPIView):
    serializer_class = FAQSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = FAQ.objects.filter(is_active=True)
        topic = self.request.query_params.get("topic")
        if topic:
            qs = qs.filter(topic=topic)
        return qs


class FAQSearchView(generics.ListAPIView):
    serializer_class = FAQSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        qs = FAQ.objects.filter(is_active=True)
        if query:
            qs = qs.filter(Q(question__icontains=query) | Q(answer__icontains=query))
        return qs


class SupportTicketCreateView(generics.CreateAPIView):
    serializer_class = SupportTicketCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
