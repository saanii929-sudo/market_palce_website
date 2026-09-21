import secrets
from decimal import Decimal

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

    class KYCStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
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

    # KYC / payout verification - submitted separately, after the seller
    # application itself has already been approved (see submit_kyc()).
    bank_account_name = models.CharField(max_length=150, blank=True)
    bank_account_number = models.CharField(max_length=50, blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    momo_number = models.CharField(max_length=20, blank=True)
    momo_network = models.CharField(max_length=30, blank=True)
    kyc_status = models.CharField(max_length=20, choices=KYCStatus.choices, default=KYCStatus.PENDING)
    kyc_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at"]

    def __str__(self):
        return f"{self.business_name} ({self.status})"


class PayoutAccount(TimeStampedModel):
    """The seller's actual, gateway-tokenized payout destination - kept
    separate from SellerApplication so it can be updated later (e.g. the
    seller switches banks) without re-running the whole KYC application.
    account_reference is a Paystack/Flutterwave transfer-recipient token,
    never a raw account/MoMo number."""

    class Type(models.TextChoices):
        BANK = "bank", "Bank account"
        MOMO = "momo", "Mobile money"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="payout_accounts")
    type = models.CharField(max_length=20, choices=Type.choices)
    account_reference = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PayoutAccount({self.seller.business_name}, {self.type})"


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


class SubscriptionPlan(TimeStampedModel):
    name = models.CharField(max_length=80)
    slug = models.SlugField(unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    billing_period_days = models.PositiveIntegerField(
        default=30, help_text="How many days one payment covers, e.g. 30 for monthly, 365 for yearly."
    )
    tagline = models.CharField(max_length=150, blank=True)
    features = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False, help_text="Highlighted as the recommended plan.")
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "price"]

    def __str__(self):
        return self.name

    @property
    def price_per_day(self) -> Decimal:
        return (self.price / self.billing_period_days).quantize(Decimal("0.01"))


def generate_subscription_reference() -> str:
    return f"SUB-{secrets.token_hex(6).upper()}"


class SellerSubscription(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACTIVE = "active", "Active"
        EXPIRED = "expired", "Expired"
        FAILED = "failed", "Failed"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    reference = models.CharField(max_length=40, unique=True, default=generate_subscription_reference)
    amount = models.DecimalField(max_digits=10, decimal_places=2, help_text="Snapshot of the plan price paid.")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    checkout_url = models.URLField(max_length=500, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"SellerSubscription({self.seller.business_name}, {self.plan.name}, {self.status})"


class BulkUploadJob(TimeStampedModel):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="bulk_upload_jobs")
    file = models.FileField(upload_to="sellers/bulk_uploads/%Y/%m/")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)
    total_rows = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    error_report = models.JSONField(
        default=list, blank=True, help_text="List of {row, errors} entries for rows that failed validation."
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"BulkUploadJob({self.seller.business_name}, {self.status})"

    @property
    def is_current(self) -> bool:
        return self.status == self.Status.ACTIVE and bool(self.expires_at) and self.expires_at > timezone.now()
