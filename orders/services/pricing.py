from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from catalog.models import Product, ProductVariant

from ..models import DeliveryMethod, TaxRule

FREE_DELIVERY_THRESHOLD = Decimal("500.00")


@dataclass
class SnapshotLine:
    product: Product
    variant: ProductVariant | None
    qty: int
    unit_price: Decimal

    @property
    def variant_id(self):
        return self.variant.id if self.variant else None

    @property
    def product_id(self):
        return self.product.id

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.qty

    @classmethod
    def from_snapshot(cls, line: dict) -> "SnapshotLine":
        return cls(
            product=Product.objects.get(id=line["product_id"]),
            variant=ProductVariant.objects.get(id=line["variant_id"]) if line["variant_id"] else None,
            qty=line["qty"],
            unit_price=Decimal(line["unit_price"]),
        )


def _round(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def free_delivery_threshold_for(seller) -> Decimal:
    return seller.min_free_delivery_threshold or FREE_DELIVERY_THRESHOLD


def group_items_by_seller(items) -> list[dict]:
    order = []
    groups: dict[int, dict] = {}
    for item in items:
        seller = item.product.seller
        if seller.id not in groups:
            groups[seller.id] = {"seller": seller, "items": []}
            order.append(seller.id)
        groups[seller.id]["items"].append(item)
    return [groups[seller_id] for seller_id in order]


def compute_coupon_discounts(groups: list[dict], coupon, cart_subtotal: Decimal, user=None) -> dict[int, Decimal]:
    result = {group["seller"].id: Decimal("0.00") for group in groups}
    if coupon is None:
        return result

    if coupon.scope == coupon.Scope.SELLER:
        target = next((g for g in groups if g["seller"].id == coupon.seller_id), None)
        if target is None:
            return result
        target_subtotal = sum((item.line_total for item in target["items"]), Decimal("0.00"))
        if coupon.validate_for_subtotal(target_subtotal, user=user) is not None:
            return result
        result[coupon.seller_id] = coupon.compute_discount(target_subtotal)
        return result

    if coupon.validate_for_subtotal(cart_subtotal, user=user) is not None:
        return result
    cart_discount = coupon.compute_discount(cart_subtotal)

    allocated = Decimal("0.00")
    for index, group in enumerate(groups):
        subtotal = sum((item.line_total for item in group["items"]), Decimal("0.00"))
        is_last = index == len(groups) - 1
        if is_last:
            amount = cart_discount - allocated
        elif cart_discount and cart_subtotal:
            amount = _round(cart_discount * subtotal / cart_subtotal)
        else:
            amount = Decimal("0.00")
        allocated += amount
        result[group["seller"].id] = amount
    return result


def price_seller_groups(
    items, *, delivery_method: DeliveryMethod, region: str, cart_subtotal: Decimal, coupon=None, user=None
) -> list[dict]:
    groups = group_items_by_seller(items)
    discounts = compute_coupon_discounts(groups, coupon, cart_subtotal, user=user)
    priced = []

    for group in groups:
        seller = group["seller"]
        group_items = group["items"]
        subtotal = sum((item.line_total for item in group_items), Decimal("0.00"))

        tax_amount = Decimal("0.00")
        for item in group_items:
            rate = TaxRule.get_rate(region, category=item.product.category)
            tax_amount += _round(item.line_total * rate)

        delivery_fee = Decimal("0.00") if subtotal >= free_delivery_threshold_for(seller) else delivery_method.price
        discount_amount = discounts.get(seller.id, Decimal("0.00"))

        total = subtotal + tax_amount + delivery_fee - discount_amount
        priced.append({
            "seller": seller,
            "items": group_items,
            "subtotal": subtotal,
            "tax_amount": tax_amount,
            "delivery_fee": delivery_fee,
            "discount_amount": discount_amount,
            "total": total,
        })

    return priced
