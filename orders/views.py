from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

import cart.services as cart_services

from .models import DeliveryMethod, Order, OrderItem, Payment, PendingCheckout, RefundRequest, SellerOrder
from .serializers import (
    CheckoutSummarySerializer,
    DeliveryMethodSerializer,
    OrderDetailSerializer,
    OrderListSerializer,
    OrderTrackingSerializer,
    PendingCheckoutSerializer,
    PlaceOrderSerializer,
    RefundEscalateSerializer,
    RefundRequestCreateSerializer,
    RefundRequestSerializer,
    RefundRequestStatusUpdateSerializer,
    SellerOrderTrackingSerializer,
    WebhookResponseSerializer,
)
from .services import checkout as checkout_service
from .services.hubtel_checkout import HubtelCheckoutError, finalize_pending_checkout, mark_pending_checkout_failed, start_hubtel_checkout
from .services.order_placement import OrderPlacementError, place_order
from .services.payment_gateway import HUBTEL_PAYMENT_METHOD_CODES, HubtelGateway, PaymentGatewayError, get_gateway
from .services.refunds import RefundError, advance_refund_request, escalate_refund_request, request_refund


class DeliveryMethodListView(generics.ListAPIView):
    queryset = DeliveryMethod.objects.filter(is_active=True)
    serializer_class = DeliveryMethodSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class CheckoutSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        parameters=[
            OpenApiParameter("delivery_method_id", int, description="Selected delivery method, for an accurate delivery fee."),
            OpenApiParameter(
                "address_id", int,
                description="Selected delivery address, for accurate per-seller tax. Defaults to your default address.",
            ),
        ],
        responses=CheckoutSummarySerializer,
    )
    def get(self, request):
        cart = cart_services.get_or_create_cart(request)

        delivery_method = None
        delivery_method_id = request.query_params.get("delivery_method_id")
        if delivery_method_id:
            delivery_method = DeliveryMethod.objects.filter(id=delivery_method_id, is_active=True).first()

        address_id = request.query_params.get("address_id")
        if address_id:
            address = request.user.addresses.filter(id=address_id).first()
        else:
            address = request.user.addresses.filter(is_default=True).first()
        region = address.region if address else ""

        totals = checkout_service.summarize(cart, delivery_method, region=region)
        return Response(CheckoutSummarySerializer(totals).data)


class OrderListCreateView(generics.ListAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get_serializer_class(self):
        return PlaceOrderSerializer if self.request.method == "POST" else OrderListSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()

        qs = Order.objects.filter(user=self.request.user).prefetch_related("seller_orders").order_by("-placed_at")
        status_param = self.request.query_params.get("status")
        if status_param:
            return [order for order in qs if order.status == status_param]
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
        return Response(data, status=status.HTTP_200_OK if already_existed else status.HTTP_201_CREATED)


class OrderDetailView(generics.RetrieveAPIView):
    serializer_class = OrderDetailSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user).prefetch_related(
            "seller_orders__seller", "seller_orders__delivery_method", "seller_orders__items__product", "payments"
        )


class OrderTrackingView(generics.RetrieveAPIView):
    serializer_class = OrderTrackingSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "order_number"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Order.objects.none()
        return Order.objects.filter(user=self.request.user).prefetch_related("seller_orders__seller")


class SellerOrderTrackingView(generics.RetrieveAPIView):
    serializer_class = SellerOrderTrackingSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_url_kwarg = "seller_order_id"
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SellerOrder.objects.none()
        return SellerOrder.objects.filter(
            order__order_number=self.kwargs["order_number"], order__user=self.request.user,
        ).select_related("seller", "shipment").prefetch_related("status_history")


class SellerOrderRateRiderView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, order_number, seller_order_id):
        from deliveries.services import TripError, get_delivery_for, rate_trip
        from riders.serializers import RateRiderSubmitSerializer

        seller_order = get_object_or_404(
            SellerOrder, id=seller_order_id, order__order_number=order_number, order__user=request.user,
        )
        serializer = RateRiderSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        delivery = get_delivery_for(seller_order)
        trip = getattr(delivery, "trip", None) if delivery else None
        if trip is None:
            return Response({"detail": "No rider trip found for this order."}, status=status.HTTP_404_NOT_FOUND)

        try:
            rating = rate_trip(
                trip, request.user,
                stars=serializer.validated_data["rating"], comment=serializer.validated_data["comment"],
            )
        except TripError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            {"id": rating.id, "rating": rating.stars, "comment": rating.comment}, status=status.HTTP_201_CREATED
        )


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
        order = get_object_or_404(
            Order.objects.prefetch_related("seller_orders"), order_number=order_number, user=request.user
        )

        seller_orders = list(order.seller_orders.all())
        cancellable = [so for so in seller_orders if so.status == SellerOrder.Status.PROCESSING]
        if not cancellable:
            return Response(
                {"detail": "Only orders that are still processing can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for seller_order in cancellable:
            seller_order.transition_to(SellerOrder.Status.CANCELLED, note="Cancelled by customer.")
        return Response(OrderDetailSerializer(order).data)


class OrderBuyAgainView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=None, responses=OpenApiTypes.OBJECT)
    def post(self, request, order_number):
        order = get_object_or_404(Order, order_number=order_number, user=request.user)
        cart = cart_services.get_or_create_cart(request)

        added, skipped = [], []
        items = OrderItem.objects.filter(seller_order__order=order).select_related("product", "variant")
        for item in items:
            if not item.product.is_active:
                skipped.append(item.product.name)
                continue
            cart_services.add_item(cart, product=item.product, variant=item.variant, qty=item.qty)
            added.append(item.product.name)

        return Response({"added": added, "skipped": skipped})


class RefundRequestCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=RefundRequestCreateSerializer, responses=RefundRequestSerializer)
    def post(self, request, order_number, item_id):
        order_item = get_object_or_404(
            OrderItem.objects.select_related("seller_order__order"),
            id=item_id, seller_order__order__order_number=order_number, seller_order__order__user=request.user,
        )

        serializer = RefundRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refund_request = request_refund(
                order_item=order_item,
                user=request.user,
                reason=serializer.validated_data["reason"],
                reason_detail=serializer.validated_data["reason_detail"],
                photos=serializer.validated_data["photos"],
                refund_type=serializer.validated_data["refund_type"],
            )
        except RefundError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RefundRequestSerializer(refund_request).data, status=status.HTTP_201_CREATED)


class RefundRequestListView(generics.ListAPIView):
    serializer_class = RefundRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RefundRequest.objects.none()

        user = self.request.user
        qs = RefundRequest.objects.select_related(
            "order_item__seller_order__order", "order_item__seller_order__seller", "order_item__product", "requested_by",
        ).prefetch_related("status_history")

        seller = getattr(user, "seller_profile", None)
        if seller is not None:
            return qs.filter(order_item__seller_order__seller=seller)
        if user.is_staff or user.is_superuser:
            return qs
        return qs.filter(requested_by=user)


class RefundRequestStatusUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=RefundRequestStatusUpdateSerializer, responses=RefundRequestSerializer)
    def patch(self, request, refund_request_id):
        refund_request = get_object_or_404(RefundRequest, id=refund_request_id)

        serializer = RefundRequestStatusUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            refund_request = advance_refund_request(
                refund_request=refund_request,
                new_status=serializer.validated_data["status"],
                actor=request.user,
                note=serializer.validated_data["note"],
                refund_amount=serializer.validated_data["refund_amount"],
            )
        except RefundError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RefundRequestSerializer(refund_request).data)


class RefundRequestEscalateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(request=RefundEscalateSerializer, responses=RefundRequestSerializer)
    def post(self, request, refund_request_id):
        refund_request = get_object_or_404(RefundRequest, id=refund_request_id)

        serializer = RefundEscalateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            escalate_refund_request(refund_request=refund_request, user=request.user, reason=serializer.validated_data["reason"])
        except RefundError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        return Response(RefundRequestSerializer(refund_request).data)


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

        if event["status"] == "success":
            from risk.tasks import check_payment_anomaly

            transaction.on_commit(lambda: check_payment_anomaly.delay(payment.id))

        order = payment.order
        if event["status"] != "success":
            for seller_order in order.seller_orders.filter(status=SellerOrder.Status.PROCESSING):
                seller_order.transition_to(SellerOrder.Status.CANCELLED, note="Payment failed.")

        return Response({"detail": "Webhook processed."})

    def _handle_hubtel_webhook(self, backend, request):
        event = backend.parse_webhook_event(request)
        reference = event.get("reference")
        pending = PendingCheckout.objects.filter(reference=reference).first()
        if pending is None:
            return Response({"detail": "Unknown reference."}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = backend.check_status(reference)
        except PaymentGatewayError:
            result = event

        if result["status"] == "success":
            finalize_pending_checkout(pending)
        elif result["status"] == "failed":
            mark_pending_checkout_failed(pending, "Payment failed or was cancelled.")

        return Response({"detail": "Webhook processed."})
