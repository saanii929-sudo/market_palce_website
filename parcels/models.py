import secrets

from django.conf import settings
from django.db import models

from accounts.models import Address
from core.models import TimeStampedModel


def generate_parcel_payment_reference() -> str:
    return f"PCL-{secrets.token_hex(6).upper()}"


class Parcel(TimeStampedModel):
    class PackageSize(models.TextChoices):
        DOCUMENT = "document", "Document"
        SMALL = "small", "Small"
        MEDIUM = "medium", "Medium"
        LARGE = "large", "Large"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RIDER_ASSIGNED = "rider_assigned", "Rider assigned"
        PICKED_UP = "picked_up", "Picked up"
        IN_TRANSIT = "in_transit", "In transit"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    CANCELLABLE_STATUSES = [Status.PENDING, Status.RIDER_ASSIGNED]

    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="parcels")
    recipient_name = models.CharField(max_length=150)
    recipient_phone = models.CharField(max_length=20)

    pickup_address = models.ForeignKey(
        Address, on_delete=models.SET_NULL, null=True, blank=True, related_name="parcel_pickups"
    )
    pickup_line1 = models.CharField(max_length=255)
    pickup_city = models.CharField(max_length=100)
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    dropoff_line1 = models.CharField(max_length=255)
    dropoff_city = models.CharField(max_length=100)
    dropoff_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    dropoff_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    package_size = models.CharField(max_length=20, choices=PackageSize.choices)
    description = models.CharField(max_length=255, blank=True)
    photo = models.ImageField(upload_to="parcels/%Y/%m/", null=True, blank=True)
    declared_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="What the sender says the contents are worth, for liability purposes only.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    price = models.DecimalField(max_digits=10, decimal_places=2)

    class PaymentStatus(models.TextChoices):
        UNPAID = "unpaid", "Unpaid"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    class PaymentMethod(models.TextChoices):
        ONLINE = "online", "Pay online"
        CASH = "cash", "Cash on pickup"

    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.ONLINE)
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.UNPAID)
    payment_reference = models.CharField(max_length=40, blank=True, default=generate_parcel_payment_reference)
    checkout_url = models.URLField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Parcel #{self.pk} ({self.status})"

    @property
    def user(self):
        return self.sender

    def can_cancel(self) -> bool:
        return self.status in self.CANCELLABLE_STATUSES


class PackageSizePricing(TimeStampedModel):
    """Admin-managed pricing rule for each package size.

    Document parcels pay a flat fee regardless of distance.
    All other sizes pay  base_fee + per_km_rate × distance_km.

    Only one active row per size is expected; if multiple exist the
    service picks the most-recently-updated one so admin can stage an
    update by creating a new row and activating it.
    """

    size = models.CharField(
        max_length=20,
        choices=Parcel.PackageSize.choices,
        db_index=True,
        help_text="The package size this rule applies to.",
    )
    label = models.CharField(
        max_length=60,
        help_text='Human-readable label shown in the app, e.g. "Document (up to A4 sheets)".',
    )
    description = models.TextField(
        blank=True,
        help_text="Optional subtitle / detail text the app can show below the size name.",
    )
    icon_url = models.URLField(
        blank=True,
        help_text="Optional icon URL the Flutter app can display next to this size.",
    )

    # Pricing fields
    flat_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text=(
            "If set, this exact amount is charged regardless of distance. "
            "Use for 'document' size. Leave blank for distance-based pricing."
        ),
    )
    base_fee = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Starting fare before distance is added. Used when flat_fee is blank.",
    )
    per_km_rate = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Additional cost per kilometre. Used when flat_fee is blank.",
    )

    is_active = models.BooleanField(
        default=True,
        help_text="Inactive rows are hidden from the Flutter app and ignored by the pricing engine.",
    )

    class Meta:
        ordering = ["size", "-updated_at"]
        verbose_name = "Package size pricing"
        verbose_name_plural = "Package size pricing"

    def __str__(self) -> str:
        if self.flat_fee is not None:
            return f"{self.get_size_display()} — flat GH₵{self.flat_fee}"
        return f"{self.get_size_display()} — GH₵{self.base_fee} + GH₵{self.per_km_rate}/km"

    # ------------------------------------------------------------------ #
    # Convenience: the price for a given distance (mirrors service logic) #
    # ------------------------------------------------------------------ #
    def compute_price(self, distance_km) -> "Decimal":
        from decimal import Decimal

        if self.flat_fee is not None:
            return self.flat_fee
        base = self.base_fee or Decimal("0.00")
        rate = self.per_km_rate or Decimal("0.00")
        dist = Decimal(str(distance_km)) if distance_km is not None else Decimal("0.00")
        return (base + rate * dist).quantize(Decimal("0.01"))
