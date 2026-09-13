from django.conf import settings
from django.db import transaction
from django.db.models import F

import cart.services as cart_services
from accounts.models import Address
from cart.models import Cart, Coupon
from catalog.models import Product

from ..models import DeliveryMethod, Order, OrderItem, OrderStatusHistory, Payment, PaymentMethod
from ..notifications import notify_order_placed
from .payment_gateway import PaymentGatewayError, get_gateway


class OrderPlacementError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _gateway_name_for(payment_method: PaymentMethod) -> str:
    if payment_method.code == "cash_on_delivery":
        return Payment.Gateway.CASH_ON_DELIVERY
    return getattr(settings, "PAYMENT_DEFAULT_GATEWAY", Payment.Gateway.PAYSTACK)


@transaction.atomic
def place_order(
    *,
    user,
    cart: Cart,
    address: Address,
    delivery_method: DeliveryMethod,
    payment_method: PaymentMethod,
    idempotency_key: str | None = None,
) -> Order:
    if idempotency_key:
        existing = Order.objects.filter(user=user, idempotency_key=idempotency_key).first()
        if existing is not None:
            return existing

    totals = cart_services.compute_totals(cart, delivery_fee=delivery_method.price)
    items = totals["items"]

    if not items:
        raise OrderPlacementError("Your cart is empty.")
    if cart.applied_coupon and totals["coupon_error"]:
        raise OrderPlacementError(totals["coupon_error"])

    order = Order(
        user=user,
        idempotency_key=idempotency_key,
        subtotal=totals["subtotal"],
        delivery_fee=totals["delivery_fee"],
        discount_amount=totals["discount_amount"],
        total=totals["total"],
        coupon=cart.applied_coupon,
        delivery_method=delivery_method,
        payment_method=payment_method,
    )
    order.snapshot_address(address)
    order.save()

    for item in items:
        stock_target = item.variant if item.variant_id else item.product
        stock_model = type(stock_target)
        decremented = stock_model.objects.filter(id=stock_target.id, stock_qty__gte=item.qty).update(
            stock_qty=F("stock_qty") - item.qty
        )
        if not decremented:
            raise OrderPlacementError(f"Not enough stock for {item.product.name}.")

        Product.objects.filter(id=item.product_id).update(sold_count=F("sold_count") + item.qty)

        OrderItem.objects.create(
            order=order, product=item.product, variant=item.variant, qty=item.qty, unit_price=item.product.price
        )

    if cart.applied_coupon:
        Coupon.objects.filter(id=cart.applied_coupon_id).update(times_used=F("times_used") + 1)

    gateway_name = _gateway_name_for(payment_method)
    try:
        result = get_gateway(gateway_name).initiate(order)
    except PaymentGatewayError as exc:
        raise OrderPlacementError(str(exc)) from exc

    payment = Payment.objects.create(
        order=order,
        gateway=gateway_name,
        gateway_reference=result["reference"],
        amount=order.total,
        status=Payment.Status.SUCCESS if gateway_name == Payment.Gateway.CASH_ON_DELIVERY else Payment.Status.PENDING,
    )

    OrderStatusHistory.objects.create(order=order, status=Order.Status.PROCESSING, note="Order placed.")

    cart.items.all().delete()
    cart.applied_coupon = None
    cart.save(update_fields=["applied_coupon"])

    notify_order_placed(order)

    order._payment_authorization_url = result.get("authorization_url")
    order._payment = payment
    return order
