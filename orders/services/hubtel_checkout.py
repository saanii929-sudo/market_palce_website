from django.db import transaction
from django.db.models import F

import cart.services as cart_services
from accounts.models import Address
from cart.models import Cart, Coupon
from catalog.models import Product, ProductVariant

from ..models import (
    DeliveryMethod,
    Order,
    OrderItem,
    OrderStatusHistory,
    Payment,
    PaymentMethod,
    PendingCheckout,
)
from ..notifications import notify_order_placed
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
    """Snapshots the cart and starts a Hubtel hosted-checkout session. No
    Order exists yet - it's only created once payment is confirmed, by
    finalize_pending_checkout()."""
    if idempotency_key:
        existing = PendingCheckout.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    totals = cart_services.compute_totals(cart, delivery_fee=delivery_method.price)
    items = totals["items"]

    if not items:
        raise HubtelCheckoutError("Your cart is empty.")
    if cart.applied_coupon and totals["coupon_error"]:
        raise HubtelCheckoutError(totals["coupon_error"])

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
        delivery_fee=totals["delivery_fee"],
        discount_amount=totals["discount_amount"],
        total=totals["total"],
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
    """Creates the real Order from a paid PendingCheckout. Idempotent - safe
    to call more than once (a webhook delivery and a client status-poll can
    both race to finalize the same reference)."""
    pending = PendingCheckout.objects.select_for_update().get(pk=pending.pk)
    if pending.status != PendingCheckout.Status.PENDING:
        return pending

    order = Order(
        user=pending.user,
        idempotency_key=pending.idempotency_key,
        subtotal=pending.subtotal,
        delivery_fee=pending.delivery_fee,
        discount_amount=pending.discount_amount,
        total=pending.total,
        coupon=pending.coupon,
        delivery_method=pending.delivery_method,
        payment_method=pending.payment_method,
    )
    order.snapshot_address(pending.address)
    order.save()

    for line in pending.cart_snapshot:
        product = Product.objects.get(id=line["product_id"])
        variant = ProductVariant.objects.get(id=line["variant_id"]) if line["variant_id"] else None
        stock_target = variant or product
        stock_model = type(stock_target)
        decremented = stock_model.objects.filter(id=stock_target.id, stock_qty__gte=line["qty"]).update(
            stock_qty=F("stock_qty") - line["qty"]
        )
        if not decremented:
            # Payment already succeeded but stock ran out in the meantime -
            # this needs a human to sort out (refund or restock), so we
            # don't silently create an unfulfillable order.
            order.delete()
            pending.status = PendingCheckout.Status.FAILED
            pending.failure_reason = (
                f"Payment for {pending.reference} succeeded but {product.name} is out of stock - needs manual refund."
            )
            pending.save(update_fields=["status", "failure_reason"])
            return pending

        Product.objects.filter(id=product.id).update(sold_count=F("sold_count") + line["qty"])
        OrderItem.objects.create(
            order=order, product=product, variant=variant, qty=line["qty"], unit_price=line["unit_price"]
        )

    if pending.coupon:
        Coupon.objects.filter(id=pending.coupon_id).update(times_used=F("times_used") + 1)

    Payment.objects.create(
        order=order,
        gateway=Payment.Gateway.HUBTEL,
        gateway_reference=pending.reference,
        amount=order.total,
        status=Payment.Status.SUCCESS,
    )
    OrderStatusHistory.objects.create(order=order, status=Order.Status.PROCESSING, note="Order placed via Hubtel payment.")

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
