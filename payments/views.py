from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet

from core.defaults import CannotDeleteOnlyDefaultError, handle_deletion

from .models import PaymentMethodToken
from .serializers import PaymentMethodTokenCreateSerializer, PaymentMethodTokenSerializer


class PaymentMethodViewSet(ModelViewSet):
    http_method_names = ["get", "post", "delete"]
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return PaymentMethodToken.objects.none()
        return PaymentMethodToken.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        return PaymentMethodTokenCreateSerializer if self.action == "create" else PaymentMethodTokenSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(user=self.request.user)
        return Response(PaymentMethodTokenSerializer(instance).data, status=status.HTTP_201_CREATED)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        try:
            handle_deletion(instance)
        except CannotDeleteOnlyDefaultError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def set_default(self, request, pk=None):
        method = self.get_object()
        method.is_default = True
        method.save(update_fields=["is_default"])
        return Response(PaymentMethodTokenSerializer(method).data)
