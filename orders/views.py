from django.conf import settings
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

import cart.services as cart_services

from .models import DeliveryMethod, Order, Payment, PendingCheckout
from .serializers import (
    CheckoutSummarySerializer,
    DeliveryMethodSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderTrackingSerializer,
    PendingCheckoutSerializer,
    PlaceOrderSerializer,
    WebhookResponseSerializer,
)
from .services import checkout as checkout_service
from .services.hubtel_checkout import HubtelCheckoutError, finalize_pending_checkout, mark_pending_checkout_failed, start_hubtel_checkout
from .services.order_placement import OrderPlacementError, place_order
from .services.payment_gateway import HUBTEL_PAYMENT_METHOD_CODES, HubtelGateway, PaymentGatewayError, get_gateway


class DeliveryMethodListView(generics.ListAPIView):
    """Active delivery methods a customer can pick at checkout, e.g. to
    populate a delivery-method selector before calling CheckoutSummaryView
    with the chosen one's id."""

    queryset = DeliveryMethod.objects.filter(is_active=True)
    serializer_class = DeliveryMethodSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class CheckoutSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=CheckoutSummarySerializer)
    def get(self, request):
        cart = cart_services.get_or_create_cart(request)

        delivery_method = None
        delivery_method_id = request.query_params.get("delivery_method_id")
        if delivery_method_id:
            delivery_method = DeliveryMethod.objects.filter(id=delivery_method_id, is_active=True).first()

        totals = checkout_service.summarize(cart, delivery_method)
        return Response(CheckoutSummarySerializer(totals).data)


class OrderListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return PlaceOrderSerializer if self.request.method == "POST" else OrderListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()

        qs = Order.objects.filter(user=self.request.user)
        status_param = self.request.query_params.get("status")
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @extend_schema(request=PlaceOrderSerializer, responses=OrderDetailSerializer)
    def post(self, request):
        serializer = PlaceOrderSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        payment_method = serializer.validated_data["payment_method"]

        idempotency_key = request.headers.get("Idempotency-Key")
        cart = cart_services.get_or_create_cart(request)

        if payment_method.code in HUBTEL_PAYMENT_METHOD_CODES:
            already_existed = bool(
                idempotency_key
                and PendingCheckout.objects.filter(user=request.user, idempotency_key=idempotency_key).exists()
            )
            try:
                pending = start_hubtel_checkout(
                    user=request.user,
                    cart=cart,
                    address=serializer.validated_data["address"],
                    delivery_method=serializer.validated_data["delivery_method"],
                    payment_method=payment_method,
                    callback_url=request.build_absolute_uri(reverse("payment-webhook", kwargs={"gateway": "hubtel"})),
                    return_url=serializer.validated_data.get("return_url") or settings.HUBTEL_RETURN_URL,
                    cancellation_url=serializer.validated_data.get("cancellation_url") or settings.HUBTEL_CANCELLATION_URL,
                    idempotency_key=idempotency_key,
                )
            except HubtelCheckoutError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

            return Response(
                PendingCheckoutSerializer(pending).data,
                status=status.HTTP_200_OK if already_existed else status.HTTP_201_CREATED,
            )

        already_existed = bool(
            idempotency_key and Order.objects.filter(user=request.user, idempotency_key=idempotency_key).exists()
        )
        try:
            order = place_order(
                user=request.user,
                cart=cart,
                address=serializer.validated_data["address"],
                delivery_method=serializer.validated_data["delivery_method"],
                payment_method=payment_method,
                idempotency_key=idempotency_key,
            )
        except OrderPlacementError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        data = OrderDetailSerializer(order).data
        data["payment_authorization_url"] = getattr(order, "_payment_authorization_url", None)
        # A retried request with the same key returns the original order
        # rather than placing a second one.
        return Response(data, status=status.HTTP_200_OK if already_existed else status.HTTP_201_CREATED)


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user)


class OrderTrackingView(generics.RetrieveAPIView):
    serializer_class = OrderTrackingSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user).prefetch_related("status_history")


class HubtelCheckoutStatusView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(responses=PendingCheckoutSerializer)
    def get(self, request):
        reference = request.query_params.get("reference")
        if not reference:
            return Response({"detail": "reference is required."}, status=status.HTTP_400_BAD_REQUEST)

        pending = get_object_or_404(PendingCheckout, reference=reference, user=request.user)

        if pending.status == PendingCheckout.Status.PENDING:
            try:
                result = HubtelGateway().check_status(reference)
            except PaymentGatewayError as exc:
                return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

            if result["status"] == "success":
                pending = finalize_pending_checkout(pending)
            elif result["status"] == "failed":
                pending = mark_pending_checkout_failed(pending, "Payment failed or was cancelled.")

        return Response(PendingCheckoutSerializer(pending).data)


class OrderCancelView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=OrderDetailSerializer)
    def post(self, request, order_number):
        order = get_object_or_404(Order, order_number=order_number, user=request.user)

        if order.status != Order.Status.PROCESSING:
            return Response(
                {"detail": "Only orders that are still processing can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order.transition_to(Order.Status.CANCELLED, note="Cancelled by customer.")
        return Response(OrderDetailSerializer(order).data)


class OrderBuyAgainView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request, order_number):
        order = get_object_or_404(Order, order_number=order_number, user=request.user)
        cart = cart_services.get_or_create_cart(request)

        added, skipped = [], []
        for item in order.items.select_related("product", "variant"):
            if not item.product.is_active:
                skipped.append(item.product.name)
                continue
            cart_services.add_item(cart, product=item.product, variant=item.variant, qty=item.qty)
            added.append(item.product.name)

        return Response({"added": added, "skipped": skipped})


class PaymentWebhookView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "gateway", str, OpenApiParameter.PATH,
                description="Payment gateway that sent this callback.",
                examples=[OpenApiExample("Hubtel", value="hubtel")],
            ),
        ],
        request=OpenApiTypes.OBJECT,
        examples=[
            OpenApiExample(
                "Hubtel callback",
                description="Hubtel's actual callback shape - see HubtelGateway.parse_webhook_event.",
                value={"Data": {"ClientReference": "HBT-A1B2C3D4E5F6", "Status": "Success"}},
                request_only=True,
            ),
        ],
        responses={
            200: WebhookResponseSerializer,
            404: WebhookResponseSerializer,
        },
        description=(
            "Hubtel's callback body isn't cryptographically signed, so for `gateway=hubtel` this "
            "always re-confirms the result against Hubtel's own status API (HubtelGateway.check_status) "
            "before finalizing the pending checkout, rather than trusting the callback payload directly. "
            "Order checkout uses this endpoint; a subscription purchase (sellers app) uses its own "
            "webhook at /sellers/subscriptions/webhook/ instead."
        ),
    )
    def post(self, request, gateway):
        try:
            backend = get_gateway(gateway)
        except Exception:
            return Response({"detail": "Unknown gateway."}, status=status.HTTP_404_NOT_FOUND)

        if gateway == "hubtel":
            return self._handle_hubtel_webhook(backend, request)

        if not backend.verify_webhook_signature(request):
            return Response({"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        event = backend.parse_webhook_event(request)
        payment = Payment.objects.filter(gateway_reference=event["reference"]).select_related("order").first()
        if payment is None:
            return Response({"detail": "Unknown payment reference."}, status=status.HTTP_404_NOT_FOUND)

        payment.status = Payment.Status.SUCCESS if event["status"] == "success" else Payment.Status.FAILED
        payment.save(update_fields=["status"])

        order = payment.order
        if event["status"] != "success" and order.status == Order.Status.PROCESSING:
            order.transition_to(Order.Status.CANCELLED, note="Payment failed.")

        return Response({"detail": "Webhook processed."})

    def _handle_hubtel_webhook(self, backend, request):
        event = backend.parse_webhook_event(request)
        reference = event.get("reference")
        pending = PendingCheckout.objects.filter(reference=reference).first()
        if pending is None:
            return Response({"detail": "Unknown reference."}, status=status.HTTP_404_NOT_FOUND)

        # Hubtel's callback body isn't cryptographically signed, so it's
        # only a trigger to check - the actual result is confirmed against
        # Hubtel's own status API before anything is finalized.
        try:
            result = backend.check_status(reference)
        except PaymentGatewayError:
            result = event

        if result["status"] == "success":
            finalize_pending_checkout(pending)
        elif result["status"] == "failed":
            mark_pending_checkout_failed(pending, "Payment failed or was cancelled.")

        return Response({"detail": "Webhook processed."})
