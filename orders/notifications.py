def notify_order_placed(order) -> None:
    from notifications.services import notify

    notify(
        order.user,
        "order_update",
        f"Order {order.order_number} placed",
        f"We've received your order for GH₵{order.total}. We'll let you know when it ships.",
    )


def notify_seller_order_status_change(seller_order) -> None:
    from notifications.services import notify

    order = seller_order.order
    status_titles = {
        "shipped": f"Your {seller_order.seller.business_name} items have shipped",
        "out_for_delivery": f"Your {seller_order.seller.business_name} items are out for delivery",
        "delivered": f"Your {seller_order.seller.business_name} items were delivered",
        "cancelled": f"Your {seller_order.seller.business_name} items were cancelled",
    }
    title = status_titles.get(seller_order.status, f"Order {order.order_number} updated")
    notify(
        order.user, "order_update", title,
        f"Order {order.order_number} ({seller_order.suborder_number}) is now {seller_order.get_status_display()}.",
    )


def notify_return_requested(return_request) -> None:
    from notifications.services import notify

    seller_order = return_request.seller_order
    order = seller_order.order
    notify(
        order.user, "order_update", "Return request submitted",
        f"We've received your return request for order {order.order_number}. We'll let you know once it's reviewed.",
    )

    seller = seller_order.seller
    if seller.user_id:
        notify(
            seller.user, "order_update", "New return request",
            f"A customer requested a return on order {order.order_number}.",
        )


def notify_return_resolved(return_request) -> None:
    from notifications.services import notify

    order = return_request.seller_order.order
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


def notify_refund_requested(refund_request) -> None:
    from notifications.services import notify

    order_item = refund_request.order_item
    order = order_item.seller_order.order
    notify(
        refund_request.requested_by, "order_update", "Refund request submitted",
        f"We've received your refund request for {order_item.product.name} (order {order.order_number}). "
        "We'll let you know once it's reviewed.",
    )

    seller = order_item.seller_order.seller
    if seller.user_id:
        notify(
            seller.user, "order_update", "New refund request",
            f"A customer requested a refund for {order_item.product.name} in order {order.order_number}.",
        )


def notify_refund_status_change(refund_request) -> None:
    from notifications.services import notify

    order_item = refund_request.order_item
    order = order_item.seller_order.order
    status_titles = {
        "under_review": "Your refund request is under review",
        "approved": "Your refund request was approved",
        "rejected": "Your refund request was declined",
        "refunded": "Your refund has been issued",
    }
    title = status_titles.get(refund_request.status, f"Refund request for {order.order_number} updated")
    body = f"{order_item.product.name} (order {order.order_number}) - now {refund_request.get_status_display()}."
    if refund_request.status == "refunded":
        body = f"GH₵{refund_request.refund_amount} has been refunded for {order_item.product.name} (order {order.order_number})."
    elif refund_request.status == "rejected" and refund_request.resolution_note:
        body = refund_request.resolution_note

    notify(refund_request.requested_by, "order_update", title, body)


def notify_refund_escalated(refund_request) -> None:
    from notifications.services import notify

    order_item = refund_request.order_item
    order = order_item.seller_order.order
    seller = order_item.seller_order.seller

    notify(
        refund_request.requested_by, "order_update", "Refund request escalated",
        f"Your refund request for {order_item.product.name} (order {order.order_number}) has been escalated for admin review.",
    )
    if seller.user_id:
        notify(
            seller.user, "order_update", "Refund request escalated",
            f"The refund request for {order_item.product.name} in order {order.order_number} has been escalated for admin review.",
        )
