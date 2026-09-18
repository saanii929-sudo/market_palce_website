from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .models import CartItem
from .serializers import (
    CartItemAddSerializer,
    CartItemSerializer,
    CartItemUpdateSerializer,
    CartSerializer,
    CouponApplySerializer,
)
from .services import CartError


def _cart_response(cart):
    totals = services.compute_totals(cart)
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
        totals = services.compute_totals(cart)
        error = coupon.validate_for_subtotal(totals["subtotal"])
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
