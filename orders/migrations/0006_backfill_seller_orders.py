# The actual data migration for the Order -> SellerOrder split: for every
# existing Order, group its OrderItems by distinct seller and create one
# SellerOrder per seller, then re-parent OrderItem/OrderStatusHistory/
# Shipment/ReturnRequest onto the correct SellerOrder. Nothing is deleted
# here and no historical Order is left without a SellerOrder - 0005 added
# the new columns as nullable specifically so this step can populate them
# before 0007 makes them required and drops the old columns.
#
# Historical orders never charged tax (TaxRule didn't exist yet), so
# tax_amount is backfilled as 0.00 - it only applies going forward.
# delivery_fee/discount_amount are recomputed per seller group using the
# same free-delivery-threshold-per-seller and proportional-discount rules
# the new checkout path uses (orders/services/pricing.py), rather than
# guessing how to split the old single combined amounts.

from decimal import ROUND_HALF_UP, Decimal

from django.db import migrations

FREE_DELIVERY_THRESHOLD = Decimal("500.00")


def _round(amount):
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _suborder_number(order_number, index):
    return f"{order_number}-{chr(65 + index)}"


def backfill_seller_orders(apps, schema_editor):
    Order = apps.get_model('orders', 'Order')
    OrderItem = apps.get_model('orders', 'OrderItem')
    OrderStatusHistory = apps.get_model('orders', 'OrderStatusHistory')
    Shipment = apps.get_model('orders', 'Shipment')
    ReturnRequest = apps.get_model('orders', 'ReturnRequest')
    ReturnRequestItem = apps.get_model('orders', 'ReturnRequestItem')
    SellerOrder = apps.get_model('orders', 'SellerOrder')

    for order in Order.objects.all().iterator():
        items = list(OrderItem.objects.filter(order=order).select_related('product__seller'))
        if not items:
            continue

        groups = {}
        seller_sequence = []
        for item in items:
            seller_id = item.product.seller_id
            if seller_id not in groups:
                groups[seller_id] = []
                seller_sequence.append(seller_id)
            groups[seller_id].append(item)

        old_subtotal = order.subtotal or Decimal("0.00")
        old_discount = order.discount_amount or Decimal("0.00")
        discount_allocated = Decimal("0.00")
        seller_orders = {}

        for index, seller_id in enumerate(seller_sequence):
            group_items = groups[seller_id]
            subtotal = sum((i.unit_price * i.qty for i in group_items), Decimal("0.00"))
            delivery_fee = Decimal("0.00") if subtotal >= FREE_DELIVERY_THRESHOLD else order.delivery_method.price

            is_last = index == len(seller_sequence) - 1
            if is_last:
                discount_amount = old_discount - discount_allocated
            elif old_discount and old_subtotal:
                discount_amount = _round(old_discount * subtotal / old_subtotal)
            else:
                discount_amount = Decimal("0.00")
            discount_allocated += discount_amount

            total = subtotal + delivery_fee - discount_amount

            seller_order = SellerOrder.objects.create(
                order=order,
                seller_id=seller_id,
                suborder_number=_suborder_number(order.order_number, index),
                status=order.status,
                subtotal=subtotal,
                tax_amount=Decimal("0.00"),
                delivery_fee=delivery_fee,
                discount_amount=discount_amount,
                total=total,
                delivery_method_id=order.delivery_method_id,
            )
            seller_orders[seller_id] = seller_order

            OrderItem.objects.filter(id__in=[i.id for i in group_items]).update(seller_order=seller_order)

        ordered_seller_orders = [seller_orders[sid] for sid in seller_sequence]
        primary = ordered_seller_orders[0]

        # A pre-split order only ever had one shared status timeline. Keep
        # the original rows (re-parented) on the first sub-order and give
        # every other sub-order an identical copy, so each one's tracking
        # page still shows the history it inherited from before the split.
        histories = list(OrderStatusHistory.objects.filter(order=order).order_by('created_at'))
        if histories:
            OrderStatusHistory.objects.filter(id__in=[h.id for h in histories]).update(seller_order=primary)
            for seller_order in ordered_seller_orders[1:]:
                for h in histories:
                    copy = OrderStatusHistory.objects.create(order=order, seller_order=seller_order, status=h.status, note=h.note)
                    OrderStatusHistory.objects.filter(id=copy.id).update(created_at=h.created_at, updated_at=h.updated_at)

        # Same story for the single old shipment record.
        shipment = Shipment.objects.filter(order=order).first()
        if shipment is not None:
            Shipment.objects.filter(id=shipment.id).update(seller_order=primary)
            for seller_order in ordered_seller_orders[1:]:
                # `order` is left null here (relaxed to nullable in 0005) -
                # the old OneToOneField only allowed one Shipment per Order,
                # so a second sub-order's copy can't also point at it.
                Shipment.objects.create(
                    seller_order=seller_order,
                    courier_name=shipment.courier_name,
                    tracking_number=shipment.tracking_number,
                    current_status=shipment.current_status,
                )

        # A return request's real owner is whichever seller shipped the
        # items it's returning - derive that from the (now re-parented)
        # order items instead of guessing.
        for rr in ReturnRequest.objects.filter(order=order):
            first_line = ReturnRequestItem.objects.filter(return_request=rr).select_related('order_item').first()
            target = first_line.order_item.seller_order if first_line else primary
            ReturnRequest.objects.filter(id=rr.id).update(seller_order=target)


def unbackfill_seller_orders(apps, schema_editor):
    """Best-effort reverse for local/dev use. Nulls every FK pointing at a
    SellerOrder via a bulk update (which, unlike .delete(), does not trigger
    on_delete cascades) before deleting SellerOrder rows, so the delete can't
    cascade into - and destroy - the original OrderItem/OrderStatusHistory/
    Shipment/ReturnRequest rows that were merely re-parented onto it. The
    OrderStatusHistory/Shipment rows that were duplicated onto secondary
    sub-orders during the forward migration are intentionally left behind
    (now with seller_order=None) rather than guessed-deleted, since nothing
    distinguishes a duplicate from the original it was copied from."""
    OrderItem = apps.get_model('orders', 'OrderItem')
    OrderStatusHistory = apps.get_model('orders', 'OrderStatusHistory')
    Shipment = apps.get_model('orders', 'Shipment')
    ReturnRequest = apps.get_model('orders', 'ReturnRequest')
    SellerOrder = apps.get_model('orders', 'SellerOrder')

    OrderItem.objects.update(seller_order=None)
    OrderStatusHistory.objects.update(seller_order=None)
    Shipment.objects.update(seller_order=None)
    ReturnRequest.objects.update(seller_order=None)
    SellerOrder.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_add_seller_order_schema'),
    ]

    operations = [
        migrations.RunPython(backfill_seller_orders, unbackfill_seller_orders),
    ]
