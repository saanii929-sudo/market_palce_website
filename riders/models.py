from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class RiderProfile(TimeStampedModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rider_profile")
    rating_avg = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    is_online = models.BooleanField(default=False)
    is_verified = models.BooleanField(
        default=False, help_text="Flips to True automatically once every required document is verified."
    )
    current_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    current_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    acceptance_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("100.00"))
    min_trip_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        help_text="Only offer this rider trips worth at least this much. Blank = no minimum.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RiderProfile({self.user})"

    def set_online(self, is_online: bool) -> None:
        if is_online and not self.is_verified:
            raise ValidationError("You must complete verification before going online.")
        self.is_online = is_online
        self.save(update_fields=["is_online"])

    def update_location(self, lat, lng) -> None:
        self.current_lat = lat
        self.current_lng = lng
        self.save(update_fields=["current_lat", "current_lng"])


class Vehicle(TimeStampedModel):
    class Type(models.TextChoices):
        BICYCLE = "bicycle", "Bicycle"
        MOTORCYCLE = "motorcycle", "Motorcycle"
        CAR = "car", "Car"
        VAN = "van", "Van"

    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="vehicles")
    type = models.CharField(max_length=20, choices=Type.choices)
    make = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=50, blank=True)
    plate_number = models.CharField(max_length=20, blank=True)
    color = models.CharField(max_length=30, blank=True)
    photo = models.ImageField(upload_to="riders/vehicles/", null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_type_display()} ({self.plate_number or 'no plate'})"


REQUIRED_DOCUMENTS_BY_VEHICLE_TYPE = {
    Vehicle.Type.BICYCLE: ["government_id", "profile_photo"],
    Vehicle.Type.MOTORCYCLE: ["government_id", "drivers_licence", "vehicle_registration", "profile_photo"],
    Vehicle.Type.CAR: ["government_id", "drivers_licence", "vehicle_registration", "profile_photo"],
    Vehicle.Type.VAN: ["government_id", "drivers_licence", "vehicle_registration", "profile_photo"],
}


class RiderDocument(TimeStampedModel):
    class DocType(models.TextChoices):
        GOVERNMENT_ID = "government_id", "Government ID"
        DRIVERS_LICENCE = "drivers_licence", "Driver's licence"
        VEHICLE_REGISTRATION = "vehicle_registration", "Vehicle registration"
        PROFILE_PHOTO = "profile_photo", "Profile photo"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        VERIFIED = "verified", "Verified"
        REJECTED = "rejected", "Rejected"

    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30, choices=DocType.choices)
    file = models.FileField(upload_to="riders/documents/%Y/%m/")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateField(null=True, blank=True, help_text="For licences/registrations that expire.")
    reviewer_note = models.CharField(max_length=255, blank=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["rider", "doc_type"], name="unique_rider_document_per_type")
        ]

    def __str__(self):
        return f"{self.rider} - {self.get_doc_type_display()} ({self.status})"

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at and self.expires_at < timezone.now().date())


class RiderPayoutAccount(TimeStampedModel):
    class Type(models.TextChoices):
        BANK = "bank", "Bank account"
        MOMO = "momo", "Mobile money"

    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="payout_accounts")
    type = models.CharField(max_length=20, choices=Type.choices)
    account_reference = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RiderPayoutAccount({self.rider}, {self.type})"


class RiderEarning(TimeStampedModel):
    trip = models.OneToOneField("deliveries.Trip", on_delete=models.CASCADE, related_name="earning")
    rider = models.ForeignKey(RiderProfile, on_delete=models.PROTECT, related_name="earnings")
    base_fare = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    distance_bonus = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    tip = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("1.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RiderEarning({self.rider}, {self.total})"


class RiderPayout(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        SCHEDULED = "scheduled", "Scheduled"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="payouts")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    payout_account = models.ForeignKey(RiderPayoutAccount, on_delete=models.PROTECT, related_name="payouts")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    admin_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"RiderPayout({self.rider}, {self.amount}, {self.status})"


class RiderRating(TimeStampedModel):
    trip = models.OneToOneField("deliveries.Trip", on_delete=models.CASCADE, related_name="rating")
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rider_ratings_given")
    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="ratings")
    stars = models.PositiveSmallIntegerField()
    comment = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=models.Q(stars__gte=1, stars__lte=5), name="rider_rating_stars_1_to_5")
        ]

    def __str__(self):
        return f"RiderRating({self.rider}, {self.stars}*)"
