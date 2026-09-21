from decimal import Decimal

from django.db import transaction
from django.db.models import F

import cart.services as cart_services
from accounts.models import Address
from cart.models import Cart, Coupon, CouponRedemption
from catalog.models import Product

from ..models import (
    DeliveryMethod,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    PaymentMethod,
    PendingCheckout,
    SellerOrder,
)
from ..notifications import notify_order_placed
from . import pricing
from .payment_gateway import HubtelGateway, PaymentGatewayError


class HubtelCheckoutError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@transaction.atomic
def start_hubtel_checkout(
    *,
    user,
    cart: Cart,
    address: Address,
    delivery_method: DeliveryMethod,
    payment_method: PaymentMethod,
    callback_url: str,
    return_url: str,
    cancellation_url: str,
    idempotency_key: str | None = None,
) -> PendingCheckout:
    if idempotency_key:
        existing = PendingCheckout.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    totals = cart_services.compute_totals(cart, delivery_fee=0)
    items = totals["items"]

    if not items:
        raise HubtelCheckoutError("Your cart is empty.")
    if cart.applied_coupon and totals["coupon_error"]:
        raise HubtelCheckoutError(totals["coupon_error"])

    seller_groups = pricing.price_seller_groups(
        items, delivery_method=delivery_method, region=address.region,
        cart_subtotal=totals["subtotal"], coupon=cart.applied_coupon, user=user,
    )
    delivery_fee = sum((g["delivery_fee"] for g in seller_groups), Decimal("0.00"))
    tax_amount = sum((g["tax_amount"] for g in seller_groups), Decimal("0.00"))
    discount_amount = sum((g["discount_amount"] for g in seller_groups), Decimal("0.00"))
    total = totals["subtotal"] - discount_amount + delivery_fee + tax_amount

    snapshot = [
        {
            "product_id": item.product_id,
            "variant_id": item.variant_id,
            "qty": item.qty,
            "unit_price": str(item.product.price),
        }
        for item in items
    ]

    pending = PendingCheckout.objects.create(
        user=user,
        address=address,
        delivery_method=delivery_method,
        payment_method=payment_method,
        coupon=cart.applied_coupon,
        cart_snapshot=snapshot,
        subtotal=totals["subtotal"],
        delivery_fee=delivery_fee,
        discount_amount=discount_amount,
        total=total,
        idempotency_key=idempotency_key,
    )

    try:
        result = HubtelGateway().initiate_checkout(
            reference=pending.reference,
            amount=pending.total,
            description=f"SportShop order for {user.email or user.phone}",
            callback_url=callback_url,
            return_url=return_url,
            cancellation_url=cancellation_url,
        )
    except PaymentGatewayError as exc:
        pending.status = PendingCheckout.Status.FAILED
        pending.failure_reason = str(exc)
        pending.save(update_fields=["status", "failure_reason"])
        raise HubtelCheckoutError(str(exc)) from exc

    pending.checkout_url = result["authorization_url"] or ""
    pending.save(update_fields=["checkout_url"])
    return pending


@transaction.atomic
def finalize_pending_checkout(pending: PendingCheckout) -> PendingCheckout:
    pending = PendingCheckout.objects.select_for_update().get(pk=pending.pk)
    if pending.status != PendingCheckout.Status.PENDING:
        return pending

    order = Order(
        user=pending.user,
        idempotency_key=pending.idempotency_key,
        coupon=pending.coupon,
        payment_method=pending.payment_method,
    )
    order.snapshot_address(pending.address)
    order.save()

    items = [pricing.SnapshotLine.from_snapshot(line) for line in pending.cart_snapshot]
    seller_groups = pricing.price_seller_groups(
        items,
        delivery_method=pending.delivery_method,
        region=pending.address.region,
        cart_subtotal=pending.subtotal,
        coupon=pending.coupon,
        user=pending.user,
    )

    for group in seller_groups:
        seller_order = SellerOrder.objects.create(
            order=order,
            seller=group["seller"],
            subtotal=group["subtotal"],
            tax_amount=group["tax_amount"],
            delivery_fee=group["delivery_fee"],
            discount_amount=group["discount_amount"],
            total=group["total"],
            delivery_method=pending.delivery_method,
        )

        for item in group["items"]:
            stock_target = item.variant or item.product
            stock_model = type(stock_target)
            decremented = stock_model.objects.filter(id=stock_target.id, stock_qty__gte=item.qty).update(
                stock_qty=F("stock_qty") - item.qty
            )
            if not decremented:
                order.delete()
                pending.status = PendingCheckout.Status.FAILED
                pending.failure_reason = (
                    f"Payment for {pending.reference} succeeded but {item.product.name} is out of "
                    "stock - needs manual refund."
                )
                pending.save(update_fields=["status", "failure_reason"])
                return pending

            Product.objects.filter(id=item.product.id).update(sold_count=F("sold_count") + item.qty)
            OrderItem.objects.create(
                seller_order=seller_order, product=item.product, variant=item.variant,
                qty=item.qty, unit_price=item.unit_price,
            )

        OrderStatusHistory.objects.create(
            seller_order=seller_order, status=Order.Status.PROCESSING, note="Order placed via Hubtel payment."
        )

    if pending.coupon:
        Coupon.objects.filter(id=pending.coupon_id).update(times_used=F("times_used") + 1)
        CouponRedemption.objects.create(coupon=pending.coupon, user=pending.user, order=order)
        from risk.tasks import check_coupon_abuse

        transaction.on_commit(lambda: check_coupon_abuse.delay(order.id))

    payment = Payment.objects.create(
        order=order,
        gateway=Payment.Gateway.HUBTEL,
        gateway_reference=pending.reference,
        amount=order.total,
        status=Payment.Status.SUCCESS,
    )
    from risk.tasks import check_payment_anomaly

    transaction.on_commit(lambda: check_payment_anomaly.delay(payment.id))

    cart = Cart.objects.filter(user=pending.user).first()
    if cart is not None:
        cart.items.all().delete()
        cart.applied_coupon = None
        cart.save(update_fields=["applied_coupon"])

    pending.status = PendingCheckout.Status.PAID
    pending.order = order
    pending.save(update_fields=["status", "order"])

    notify_order_placed(order)
    return pending


def mark_pending_checkout_failed(pending: PendingCheckout, reason: str) -> PendingCheckout:
    if pending.status != PendingCheckout.Status.PENDING:
        return pending
    pending.status = PendingCheckout.Status.FAILED
    pending.failure_reason = reason
    pending.save(update_fields=["status", "failure_reason"])
    return pending
