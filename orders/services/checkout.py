from decimal import Decimal

import cart.services as cart_services
from cart.models import Cart

from ..models import DeliveryMethod
from . import pricing


def summarize(cart: Cart, delivery_method: DeliveryMethod | None, region: str = "") -> dict:
    totals = cart_services.compute_totals(cart, delivery_fee=Decimal("0.00"))
    items = totals["items"]

    if not items or delivery_method is None:
        totals["tax_amount"] = Decimal("0.00")
        return totals

    seller_groups = pricing.price_seller_groups(
        items,
        delivery_method=delivery_method,
        region=region,
        cart_subtotal=totals["subtotal"],
        coupon=cart.applied_coupon,
        user=cart.user,
    )
    delivery_fee = sum((g["delivery_fee"] for g in seller_groups), Decimal("0.00"))
    tax_amount = sum((g["tax_amount"] for g in seller_groups), Decimal("0.00"))
    discount_amount = sum((g["discount_amount"] for g in seller_groups), Decimal("0.00"))

    totals["delivery_fee"] = delivery_fee
    totals["tax_amount"] = tax_amount
    totals["discount_amount"] = discount_amount
    totals["total"] = totals["subtotal"] - discount_amount + delivery_fee + tax_amount
    return totals
