from django.conf import settings
from django.db import models
from django.utils import timezone

from catalog.models import Category, Seller
from core.models import TimeStampedModel


class SellerApplication(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="seller_applications"
    )
    business_name = models.CharField(max_length=150)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="seller_applications")
    phone = models.CharField(max_length=20)

    id_document = models.FileField(
        upload_to="seller_applications/ids/%Y/%m/", blank=True,
        help_text="Government-issued ID (e.g. national ID, passport) - required before review.",
    )
    business_certificate = models.FileField(
        upload_to="seller_applications/certificates/%Y/%m/", blank=True,
        help_text="Business registration certificate, if the business is formally registered.",
    )

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField(default=timezone.now)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewer_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.business_name} ({self.status})"


class Payout(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="payouts")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=50, blank=True)
    account_details = models.CharField(
        max_length=255, blank=True, help_text="Destination account/number for this payout, e.g. mobile money number."
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    payout_date = models.DateField(null=True, blank=True, help_text="Set once the payout is scheduled or paid.")
    admin_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payout({self.seller.business_name}, {self.amount}, {self.status})"
