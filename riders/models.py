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
        if is_online == self.is_online:
            return
        self.is_online = is_online
        self.save(update_fields=["is_online"])

        if is_online:
            RiderOnlineSession.objects.create(rider=self)
            # Kick off dispatch for any deliveries that searched for a
            # rider before this rider came online and found nobody.
            # Runs on_commit so the is_online=True write is visible to the
            # queries inside dispatch_delivery before it runs.
            from django.db import transaction
            transaction.on_commit(
                lambda: _dispatch_pending_nearby_deliveries_for_rider(self), robust=True
            )
        else:
            session = self.online_sessions.filter(ended_at__isnull=True).order_by("-started_at").first()
            if session is not None:
                session.ended_at = timezone.now()
                session.save(update_fields=["ended_at"])

    def update_location(self, lat, lng) -> None:
        self.current_lat = lat
        self.current_lng = lng
        self.save(update_fields=["current_lat", "current_lng"])


def _dispatch_pending_nearby_deliveries_for_rider(rider_profile: "RiderProfile") -> None:
    """Called on_commit when a rider comes online. Finds PENDING deliveries
    that exhausted their initial search (dispatch_attempts > 0 but no active
    offer) and re-runs dispatch so they get a chance to be matched now that
    this rider is available. Runs in a best-effort background thread so a
    slow query never blocks the status toggle response."""
    import logging
    import threading

    logger = logging.getLogger(__name__)

    def _run():
        try:
            from deliveries.models import Delivery, DeliveryOffer
            from deliveries.services import DEFAULT_SEARCH_RADIUS_KM, _bounding_box, dispatch_delivery, haversine_km

            if rider_profile.current_lat is None or rider_profile.current_lng is None:
                return

            lat = float(rider_profile.current_lat)
            lng = float(rider_profile.current_lng)
            min_lat, max_lat, min_lng, max_lng = _bounding_box(lat, lng, DEFAULT_SEARCH_RADIUS_KM)

            # Find PENDING deliveries with pickup coordinates in the rider's
            # vicinity that have attempted dispatch at least once (so we know
            # find_nearest_eligible_rider already ran and found nobody) but
            # have no live offer currently waiting on a rider.
            pending_ids_with_live_offer = DeliveryOffer.objects.filter(
                status=DeliveryOffer.Status.PENDING,
            ).values_list("delivery_id", flat=True)

            deliveries = Delivery.objects.filter(
                status=Delivery.Status.PENDING,
                dispatch_attempts__gt=0,
                pickup_lat__gte=min_lat, pickup_lat__lte=max_lat,
                pickup_lng__gte=min_lng, pickup_lng__lte=max_lng,
            ).exclude(id__in=pending_ids_with_live_offer)

            for delivery in deliveries:
                try:
                    dispatch_delivery(delivery)
                except Exception:
                    logger.exception(
                        "dispatch_delivery failed for delivery %s during rider-online trigger", delivery.pk
                    )
        except Exception:
            logger.exception("_dispatch_pending_nearby_deliveries_for_rider failed for rider %s", rider_profile.pk)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


class RiderOnlineSession(TimeStampedModel):
    """One row per online-toggle-on -> toggle-off span, so 'online hours
    today' on the earnings screen is a real aggregate instead of a guess.
    ended_at is null while the rider is still online."""

    rider = models.ForeignKey(RiderProfile, on_delete=models.CASCADE, related_name="online_sessions")
    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"RiderOnlineSession({self.rider}, {self.started_at})"


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
    provider = models.CharField(max_length=100, blank=True, help_text="e.g. 'MTN Mobile Money', 'GCB Bank'.")
    masked_number = models.CharField(
        max_length=30, blank=True, help_text="Display-only, e.g. '•••• 1122' - never the full account/MoMo number.",
    )
    account_reference = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    is_default = models.BooleanField(default=False)

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
