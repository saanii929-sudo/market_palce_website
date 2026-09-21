from decimal import Decimal

from django.db import transaction

from catalog.models import Product, ProductVariant
from orders.models import DeliveryMethod
from orders.services import pricing

from ..models import Cart, CartItem


def get_or_create_cart(request) -> Cart:
    if request.user.is_authenticated:
        cart, _ = Cart.objects.get_or_create(user=request.user)
        return cart

    if not request.session.session_key:
        request.session.save()
    session_key = request.session.session_key
    cart, _ = Cart.objects.get_or_create(session_key=session_key)
    return cart


def merge_guest_cart_into_user_cart(session_key: str, user) -> None:
    try:
        guest_cart = Cart.objects.get(session_key=session_key)
    except Cart.DoesNotExist:
        return

    user_cart, _ = Cart.objects.get_or_create(user=user)
    with transaction.atomic():
        for item in guest_cart.items.all():
            existing = user_cart.items.filter(product=item.product, variant=item.variant).first()
            if existing:
                existing.qty += item.qty
                existing.save(update_fields=["qty"])
            else:
                item.cart = user_cart
                item.save(update_fields=["cart"])
        if user_cart.applied_coupon_id is None and guest_cart.applied_coupon_id:
            user_cart.applied_coupon = guest_cart.applied_coupon
            user_cart.save(update_fields=["applied_coupon"])
        guest_cart.delete()


class CartError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def add_item(cart: Cart, product: Product, variant: ProductVariant | None, qty: int) -> CartItem:
    stock_target = variant or product
    existing = CartItem.objects.filter(cart=cart, product=product, variant=variant).first()
    if existing is None and qty > stock_target.stock_qty:
        raise CartError(f"Only {stock_target.stock_qty} left in stock." if stock_target.stock_qty else "Out of stock.")
    if existing is not None and existing.qty + qty > stock_target.stock_qty:
        raise CartError(f"Only {stock_target.stock_qty} left in stock - you already have {existing.qty} in your cart.")

    item, created = CartItem.objects.get_or_create(cart=cart, product=product, variant=variant, defaults={"qty": qty})
    if not created:
        item.qty += qty
        item.save(update_fields=["qty"])
    return item


def update_item_qty(item: CartItem, qty: int) -> CartItem:
    stock_target = item.variant or item.product
    if qty > stock_target.stock_qty:
        raise CartError(f"Only {stock_target.stock_qty} left in stock.")
    item.qty = qty
    item.save(update_fields=["qty"])
    return item


def compute_totals(cart: Cart, delivery_fee: Decimal = Decimal("0.00")) -> dict:
    items = list(cart.items.select_related("product__seller", "variant"))
    subtotal = sum((item.line_total for item in items), Decimal("0.00"))

    discount_amount = Decimal("0.00")
    coupon_error = None
    coupon = cart.applied_coupon
    if coupon:
        if coupon.scope == coupon.Scope.SELLER:
            coupon_subtotal = sum(
                (i.line_total for i in items if i.product.seller_id == coupon.seller_id), Decimal("0.00")
            )
        else:
            coupon_subtotal = subtotal
        coupon_error = coupon.validate_for_subtotal(coupon_subtotal, user=cart.user)
        if coupon_error is None:
            discount_amount = coupon.compute_discount(coupon_subtotal)

    total = subtotal - discount_amount + delivery_fee
    return {
        "items": items,
        "subtotal": subtotal,
        "discount_amount": discount_amount,
        "delivery_fee": delivery_fee,
        "total": total,
        "coupon_error": coupon_error,
    }


def group_cart_by_seller(cart: Cart, delivery_method: DeliveryMethod | None = None) -> list[dict]:
    """Per-seller breakdown of the cart - each seller ships and prices
    delivery independently, so the free-delivery threshold is evaluated
    against that seller's own subtotal, not the combined cart. Mirrors the
    grouping orders.services.pricing uses at checkout, so what the cart
    preview shows and what checkout actually charges never diverge."""
    if delivery_method is None:
        delivery_method = DeliveryMethod.objects.filter(is_active=True).order_by("price").first()

    items = list(cart.items.select_related("product__seller", "variant"))
    groups = pricing.group_items_by_seller(items)

    result = []
    for group in groups:
        seller = group["seller"]
        group_items = group["items"]
        subtotal = sum((item.line_total for item in group_items), Decimal("0.00"))
        free_delivery_threshold_met = subtotal >= pricing.free_delivery_threshold_for(seller)
        delivery_fee = Decimal("0.00")
        if not free_delivery_threshold_met and delivery_method is not None:
            delivery_fee = delivery_method.price

        result.append({
            "seller": seller,
            "items": group_items,
            "subtotal": subtotal,
            "delivery_fee": delivery_fee,
            "free_delivery_threshold_met": free_delivery_threshold_met,
        })
    return result
