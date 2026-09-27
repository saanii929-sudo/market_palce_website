from decimal import Decimal
from django.db import migrations


def backfill_document_price(apps, schema_editor):
    """
    Raise the price on every PENDING document parcel from GH₵10.00 to GH₵15.00.
    Only touches rows that still have the old flat fee and haven't been picked up
    yet — so customer-facing charges are corrected before a rider is assigned.
    Delivered / cancelled parcels are left as-is; their charges are already settled.
    """
    Parcel = apps.get_model("parcels", "Parcel")
    Parcel.objects.filter(
        package_size="document",
        price=Decimal("10.00"),
        status__in=["pending", "rider_assigned"],
    ).update(price=Decimal("15.00"))


def reverse_backfill_document_price(apps, schema_editor):
    """Roll back: restore GH₵15.00 → GH₵10.00 on the same rows."""
    Parcel = apps.get_model("parcels", "Parcel")
    Parcel.objects.filter(
        package_size="document",
        price=Decimal("15.00"),
        status__in=["pending", "rider_assigned"],
    ).update(price=Decimal("10.00"))


class Migration(migrations.Migration):

    dependencies = [
        ("parcels", "0003_parcel_payment_method"),
    ]

    operations = [
        migrations.RunPython(
            backfill_document_price,
            reverse_code=reverse_backfill_document_price,
        ),
    ]
