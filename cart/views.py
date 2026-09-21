from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import CartItem, Coupon
from .serializers import (
    CartItemAddSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    CouponApplySerializer,
    PromotionSerializer,
)
from .services import CartError


def _cart_response(cart):
    seller_groups = services.group_cart_by_seller(cart)
    delivery_fee = sum((g["delivery_fee"] for g in seller_groups), Decimal("0.00"))
    totals = services.compute_totals(cart, delivery_fee=delivery_fee)
    totals["seller_groups"] = seller_groups
    data = CartSerializer(totals, context={"cart": cart}).data
    return Response(data)


class CartView(APIView):
    serializer_class = CartSerializer
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        cart = services.get_or_create_cart(request)
        return _cart_response(cart)


class CartItemListCreateView(APIView):
    serializer_class = CartItemAddSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = CartItemAddSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        cart = services.get_or_create_cart(request)
        try:
            services.add_item(
                cart,
                product=serializer.validated_data["product"],
                variant=serializer.validated_data["variant"],
                qty=serializer.validated_data["qty"],
            )
        except CartError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return _cart_response(cart)


class CartItemDetailView(APIView):
    serializer_class = CartItemUpdateSerializer
    permission_classes = [permissions.AllowAny]

    def get_item(self, request, item_id):
        cart = services.get_or_create_cart(request)
        return CartItem.objects.filter(cart=cart, id=item_id).first()

    def patch(self, request, item_id):
        item = self.get_item(request, item_id)
        if item is None:
            return Response({"detail": "Cart item not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = CartItemUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            services.update_item_qty(item, serializer.validated_data["qty"])
        except CartError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return _cart_response(item.cart)

    def delete(self, request, item_id):
        item = self.get_item(request, item_id)
        if item is None:
            return Response({"detail": "Cart item not found."}, status=status.HTTP_404_NOT_FOUND)

        cart = item.cart
        item.delete()
        return _cart_response(cart)


class CartClearView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=None, responses=CartSerializer)
    def post(self, request):
        cart = services.get_or_create_cart(request)
        cart.items.all().delete()
        return _cart_response(cart)


class CouponApplyView(APIView):
    serializer_class = CouponApplySerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = CouponApplySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        coupon = serializer.validated_data["code"]

        cart = services.get_or_create_cart(request)
        items = list(cart.items.select_related("product__seller"))
        subtotal = sum((i.line_total for i in items), Decimal("0.00"))

        if coupon.scope == coupon.Scope.SELLER:
            seller_items = [i for i in items if i.product.seller_id == coupon.seller_id]
            if not seller_items:
                return Response(
                    {"detail": f"This coupon only applies to {coupon.seller.business_name} - you don't have any of their items in your cart."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            relevant_subtotal = sum((i.line_total for i in seller_items), Decimal("0.00"))
        else:
            relevant_subtotal = subtotal

        user = request.user if request.user.is_authenticated else None
        error = coupon.validate_for_subtotal(relevant_subtotal, user=user)
        if error:
            return Response({"detail": error}, status=status.HTTP_400_BAD_REQUEST)

        cart.applied_coupon = coupon
        cart.save(update_fields=["applied_coupon"])
        return _cart_response(cart)

    def delete(self, request):
        cart = services.get_or_create_cart(request)
        cart.applied_coupon = None
        cart.save(update_fields=["applied_coupon"])
        return _cart_response(cart)


class PromotionListView(generics.ListAPIView):
    serializer_class = PromotionSerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Coupon.objects.none()

        now = timezone.now()
        return (
            Coupon.objects.filter(is_public=True, is_active=True)
            .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=now))
            .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=now))
            .select_related("seller")
            .order_by("-created_at")
        )
