import cart.services as cart_services
from cart.models import Cart

from ..models import DeliveryMethod


def summarize(cart: Cart, delivery_method: DeliveryMethod | None) -> dict:
    delivery_fee = delivery_method.price if delivery_method else 0
    return cart_services.compute_totals(cart, delivery_fee=delivery_fee)
