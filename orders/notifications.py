"""Order/shipment notifications, now backed by the real notifications app
(Phase 7). Imports are local to the functions to keep orders -> notifications
a lazy, call-time dependency rather than a module-load-time one."""


def notify_order_placed(order) -> None:
    from notifications.services import notify

    notify(
        order.user,
        "order_update",
        f"Order {order.order_number} placed",
        f"We've received your order for {order.total}. We'll let you know when it ships.",
    )


def notify_order_status_change(order) -> None:
    from notifications.services import notify

    status_titles = {
        "shipped": f"Order {order.order_number} has shipped",
        "out_for_delivery": f"Order {order.order_number} is out for delivery",
        "delivered": f"Order {order.order_number} was delivered",
        "cancelled": f"Order {order.order_number} was cancelled",
    }
    title = status_titles.get(order.status, f"Order {order.order_number} updated")
    notify(order.user, "order_update", title, f"Your order status is now {order.get_status_display()}.")


def notify_return_requested(return_request) -> None:
    from notifications.services import notify

    order = return_request.order
    notify(
        order.user, "order_update", "Return request submitted",
        f"We've received your return request for order {order.order_number}. We'll let you know once it's reviewed.",
    )

    notified_sellers = set()
    for item in return_request.items.select_related("order_item__product__seller__user"):
        seller = item.order_item.product.seller
        if seller.user_id and seller.user_id not in notified_sellers:
            notify(
                seller.user, "order_update", "New return request",
                f"A customer requested a return on order {order.order_number}.",
            )
            notified_sellers.add(seller.user_id)


def notify_return_resolved(return_request) -> None:
    from notifications.services import notify

    order = return_request.order
    if return_request.status == "approved":
        notify(
            return_request.user, "order_update", "Return approved",
            f"Your return for order {order.order_number} was approved. GH₵{return_request.refund_amount} will be refunded.",
        )
    else:
        notify(
            return_request.user, "order_update", "Return request declined",
            return_request.seller_note or f"Your return request for order {order.order_number} wasn't approved.",
        )
