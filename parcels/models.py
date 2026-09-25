from django.conf import settings
from django.db import models

from accounts.models import Address
from core.models import TimeStampedModel


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

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Parcel #{self.pk} ({self.status})"

    @property
    def user(self):
        return self.sender

    def can_cancel(self) -> bool:
        return self.status in self.CANCELLABLE_STATUSES
