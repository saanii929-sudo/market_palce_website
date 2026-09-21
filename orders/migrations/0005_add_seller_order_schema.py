# Phase 1 of the Order -> Order+SellerOrder split: purely additive schema
# changes. Old Order pricing/status/delivery_method fields and the old
# `order` FKs on OrderItem/OrderStatusHistory/Shipment/ReturnRequest are left
# in place so 0006 can read them to backfill SellerOrder rows. They are only
# removed in 0007, once every historical row has been re-parented.

import django.db.models.deletion
import django.utils.timezone
from decimal import Decimal
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0008_category_commission_rate'),
        ('orders', '0004_alter_payment_gateway_pendingcheckout'),
    ]

    operations = [
        migrations.AlterField(
            model_name='orderstatushistory',
            name='status',
            field=models.CharField(choices=[('processing', 'Processing'), ('shipped', 'Shipped'), ('out_for_delivery', 'Out for delivery'), ('delivered', 'Delivered'), ('cancelled', 'Cancelled'), ('partial', 'Partially delivered')], max_length=20),
        ),
        # Order was a required, unique-per-order OneToOneField. Relaxed to
        # nullable here purely as a transitional step: 0006 needs to create
        # one Shipment per SellerOrder, and a pre-split Order with more than
        # one seller can't have more than one Shipment row pointing at the
        # same order under the old unique constraint. 0007 drops this field
        # entirely once every row has been re-parented onto seller_order.
        migrations.AlterField(
            model_name='shipment',
            name='order',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='shipment', to='orders.order'),
        ),
        migrations.CreateModel(
            name='TaxRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('region', models.CharField(help_text="e.g. 'Greater Accra' - matches Address.region.", max_length=100)),
                ('rate', models.DecimalField(decimal_places=4, help_text="e.g. 0.1250 for Ghana's 12.5% VAT.", max_digits=6)),
                ('effective_from', models.DateField(default=django.utils.timezone.now)),
                ('category', models.ForeignKey(blank=True, help_text='Leave blank for a region-wide rate covering every category.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='tax_rules', to='catalog.category')),
            ],
            options={
                'ordering': ['-effective_from'],
            },
        ),
        migrations.CreateModel(
            name='SellerOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('suborder_number', models.CharField(blank=True, max_length=30, unique=True)),
                ('status', models.CharField(choices=[('processing', 'Processing'), ('shipped', 'Shipped'), ('out_for_delivery', 'Out for delivery'), ('delivered', 'Delivered'), ('cancelled', 'Cancelled'), ('partial', 'Partially delivered')], default='processing', max_length=20)),
                ('subtotal', models.DecimalField(decimal_places=2, max_digits=10)),
                ('tax_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('delivery_fee', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('discount_amount', models.DecimalField(decimal_places=2, default=Decimal('0.00'), max_digits=10)),
                ('total', models.DecimalField(decimal_places=2, max_digits=10)),
                ('delivery_method', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='seller_orders', to='orders.deliverymethod')),
                ('order', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='seller_orders', to='orders.order')),
                ('seller', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='seller_orders', to='catalog.seller')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        # Nullable for now - 0006 backfills every historical row, then 0007
        # tightens these to their real (non-null) constraints.
        migrations.AddField(
            model_name='orderitem',
            name='seller_order',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='items', to='orders.sellerorder'),
        ),
        migrations.AddField(
            model_name='orderstatushistory',
            name='seller_order',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='status_history', to='orders.sellerorder'),
        ),
        migrations.AddField(
            model_name='shipment',
            name='seller_order',
            field=models.OneToOneField(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='shipment', to='orders.sellerorder'),
        ),
        migrations.AddField(
            model_name='returnrequest',
            name='seller_order',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, related_name='return_requests', to='orders.sellerorder'),
        ),
    ]
