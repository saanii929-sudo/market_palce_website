# Final phase of the Order -> SellerOrder split: now that 0006 has backfilled
# a SellerOrder for every historical Order and re-parented every child row,
# it's safe to drop the old columns and make the new FKs required.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_backfill_seller_orders'),
    ]

    operations = [
        migrations.RemoveField(model_name='orderitem', name='order'),
        migrations.RemoveField(model_name='orderstatushistory', name='order'),
        migrations.RemoveField(model_name='shipment', name='order'),
        migrations.RemoveField(model_name='returnrequest', name='order'),
        migrations.RemoveField(model_name='order', name='status'),
        migrations.RemoveField(model_name='order', name='subtotal'),
        migrations.RemoveField(model_name='order', name='delivery_fee'),
        migrations.RemoveField(model_name='order', name='discount_amount'),
        migrations.RemoveField(model_name='order', name='total'),
        migrations.RemoveField(model_name='order', name='delivery_method'),
        migrations.AlterField(
            model_name='orderitem',
            name='seller_order',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='orders.sellerorder'),
        ),
        migrations.AlterField(
            model_name='orderstatushistory',
            name='seller_order',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='status_history', to='orders.sellerorder'),
        ),
        migrations.AlterField(
            model_name='shipment',
            name='seller_order',
            field=models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='shipment', to='orders.sellerorder'),
        ),
        migrations.AlterField(
            model_name='returnrequest',
            name='seller_order',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='return_requests', to='orders.sellerorder'),
        ),
    ]
