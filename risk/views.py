from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RiskFlag
from .serializers import RiskFlagReviewSerializer, RiskFlagSerializer
from .services import RiskFlagError, review_risk_flag


class RiskFlagListView(generics.ListAPIView):
    serializer_class = RiskFlagSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RiskFlag.objects.none()

        qs = RiskFlag.objects.select_related("content_type", "reviewed_by")

        flag_type = self.request.query_params.get("flag_type")
        if flag_type:
            qs = qs.filter(flag_type=flag_type)

        reviewed = self.request.query_params.get("reviewed")
        if reviewed is not None:
            qs = qs.filter(reviewed=reviewed.lower() in ("1", "true", "yes"))

        return qs


class RiskFlagReviewView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(request=RiskFlagReviewSerializer, responses=RiskFlagSerializer)
    def patch(self, request, risk_flag_id):
        risk_flag = get_object_or_404(RiskFlag, id=risk_flag_id)

        serializer = RiskFlagReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            risk_flag = review_risk_flag(
                risk_flag=risk_flag, admin_user=request.user, action=serializer.validated_data["action"]
            )
        except RiskFlagError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RiskFlagSerializer(risk_flag).data)
