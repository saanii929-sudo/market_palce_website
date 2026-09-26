from decimal import Decimal

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.utils import timezone
import secrets
from core.models import TimeStampedModel
from orders.models import SellerOrder
from parcels.models import Parcel

OFFER_TTL_SECONDS = 12
MAX_DISPATCH_ATTEMPTS = 5

# The rider's pay is a straight split of what was actually charged for
# this delivery (self.price - a SellerOrder's delivery_fee, or a Parcel's
# price) - not an independent distance-based formula. The rider keeps
# RIDER_COMMISSION_RATE of that, floored at RIDER_MINIMUM_FARE so a very
# cheap/short delivery still pays something reasonable; the platform keeps
# the rest.
RIDER_COMMISSION_RATE = Decimal("0.75")
RIDER_MINIMUM_FARE = Decimal("5.00")
RIDER_SURGE_MULTIPLIER = Decimal("1.00")


class Delivery(TimeStampedModel):
    class DeliveryType(models.TextChoices):
        MARKETPLACE_ORDER = "marketplace_order", "Marketplace order"
        PARCEL = "parcel", "Parcel"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        OFFERED = "offered", "Offered"
        ACCEPTED = "accepted", "Accepted"
        IN_PROGRESS = "in_progress", "In progress"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    class InitiatedBy(models.TextChoices):
        SYSTEM = "system", "System"
        SELLER = "seller", "Seller"

    class RequestMode(models.TextChoices):
        AUTO = "auto", "Auto"
        DIRECT = "direct", "Direct"

    delivery_type = models.CharField(max_length=30, choices=DeliveryType.choices)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveBigIntegerField()
    content_object = GenericForeignKey("content_type", "object_id")

    initiated_by = models.CharField(max_length=20, choices=InitiatedBy.choices, default=InitiatedBy.SYSTEM)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="requested_deliveries",
        help_text="The seller who manually requested a rider, if any - blank for system-triggered dispatch.",
    )
    request_mode = models.CharField(
        max_length=20, choices=RequestMode.choices, default=RequestMode.AUTO,
        help_text="'direct' offers never cascade to nearest-match on expiry/decline - see cancel_delivery/expire_offer.",
    )

    pickup_address = models.CharField(max_length=255)
    pickup_contact_name = models.CharField(max_length=150)
    pickup_contact_phone = models.CharField(max_length=20)
    pickup_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    pickup_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    dropoff_address = models.CharField(max_length=255)
    dropoff_contact_name = models.CharField(max_length=150)
    dropoff_contact_phone = models.CharField(max_length=20)
    dropoff_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    dropoff_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)

    distance_km = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    dispatch_attempts = models.PositiveIntegerField(
        default=0, help_text="How many riders this has been offered to so far - capped at MAX_DISPATCH_ATTEMPTS."
    )

    rider_base_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rider_distance_bonus = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rider_surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True)
    rider_fare = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["content_type", "object_id"])]
        verbose_name_plural = "deliveries"

    def __str__(self):
        return f"Delivery #{self.pk} ({self.status})"

    @property
    def no_riders_available(self) -> bool:
        return self.status == self.Status.PENDING and self.dispatch_attempts >= MAX_DISPATCH_ATTEMPTS

    def lock_rider_fare(self) -> None:
        """Computes and freezes the rider's payout for this delivery the
        first time it's offered - a percentage split of self.price (see
        RIDER_COMMISSION_RATE above), never recomputed afterwards even
        across retries to different riders or if the price/distance
        estimate shifts. rider_distance_bonus is always 0.00 now (kept for
        schema/earnings-breakdown compatibility) - pay is a price split,
        not a distance-based formula."""
        if self.rider_fare is not None:
            return

        commission = (self.price * RIDER_COMMISSION_RATE).quantize(Decimal("0.01"))
        total = (max(commission, RIDER_MINIMUM_FARE) * RIDER_SURGE_MULTIPLIER).quantize(Decimal("0.01"))

        self.rider_base_fare = total
        self.rider_distance_bonus = Decimal("0.00")
        self.rider_surge_multiplier = RIDER_SURGE_MULTIPLIER
        self.rider_fare = total
        self.save(update_fields=["rider_base_fare", "rider_distance_bonus", "rider_surge_multiplier", "rider_fare"])

    @property
    def customer_user(self):
        obj = self.content_object
        if obj is None:
            return None
        order = getattr(obj, "order", None)
        if order is not None:
            return getattr(order, "user", None)
        return getattr(obj, "user", None)

    def advance_content_to_delivered(self) -> None:
        
        obj = self.content_object
        if isinstance(obj, SellerOrder):
            if obj.status == SellerOrder.Status.SHIPPED:
                obj.transition_to(SellerOrder.Status.OUT_FOR_DELIVERY, note="Picked up by rider.")
            if obj.status == SellerOrder.Status.OUT_FOR_DELIVERY:
                obj.transition_to(SellerOrder.Status.DELIVERED, note="Delivered by rider.")
        elif isinstance(obj, Parcel):
            obj.status = Parcel.Status.DELIVERED
            obj.save(update_fields=["status"])

        self.status = self.Status.DELIVERED
        self.save(update_fields=["status"])

    def advance_content_to_rider_assigned(self) -> None:
        

        obj = self.content_object
        if isinstance(obj, Parcel):
            obj.status = Parcel.Status.RIDER_ASSIGNED
            obj.save(update_fields=["status"])

    def advance_content_to_picked_up(self) -> None:
        obj = self.content_object
        if isinstance(obj, Parcel):
            obj.status = Parcel.Status.PICKED_UP
            update_fields = ["status"]
            # Cash-on-pickup parcels never go through the online checkout
            # flow - the sender hands the rider cash for the full price at
            # exactly this moment, so this is where payment_status flips to
            # paid. See parcels.services.find_rider_for_parcel, which lets a
            # cash parcel dispatch without waiting on this.
            if obj.payment_method == Parcel.PaymentMethod.CASH and obj.payment_status == Parcel.PaymentStatus.UNPAID:
                obj.payment_status = Parcel.PaymentStatus.PAID
                update_fields.append("payment_status")
            obj.save(update_fields=update_fields)

    def advance_content_to_in_transit(self) -> None:
       

        obj = self.content_object
        if isinstance(obj, Parcel):
            obj.status = Parcel.Status.IN_TRANSIT
            obj.save(update_fields=["status"])

    def advance_content_to_cancelled(self) -> None:
       

        obj = self.content_object
        if isinstance(obj, Parcel):
            obj.status = Parcel.Status.CANCELLED
            obj.save(update_fields=["status"])


class DeliveryOffer(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"
        EXPIRED = "expired", "Expired"

    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name="offers")
    rider = models.ForeignKey("riders.RiderProfile", on_delete=models.CASCADE, related_name="delivery_offers")
    sent_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)

    class Meta:
        ordering = ["-sent_at"]

    def __str__(self):
        return f"Offer(delivery={self.delivery_id}, rider={self.rider_id}, {self.status})"

    @property
    def seconds_remaining(self) -> int:
        remaining = (self.expires_at - timezone.now()).total_seconds()
        return max(0, int(remaining))


class Trip(TimeStampedModel):
    class Status(models.TextChoices):
        HEADING_TO_PICKUP = "heading_to_pickup", "Heading to pickup"
        PICKED_UP = "picked_up", "Picked up"
        HEADING_TO_DROPOFF = "heading_to_dropoff", "Heading to dropoff"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    ACTIVE_STATUSES = [
        Status.HEADING_TO_PICKUP, Status.PICKED_UP, Status.HEADING_TO_DROPOFF,
    ]

    delivery = models.OneToOneField(Delivery, on_delete=models.CASCADE, related_name="trip")
    rider = models.ForeignKey("riders.RiderProfile", on_delete=models.PROTECT, related_name="trips")
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.HEADING_TO_PICKUP)
    picked_up_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Trip(delivery={self.delivery_id}, rider={self.rider_id}, {self.status})"


class TripStatusHistory(TimeStampedModel):
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=30, choices=Trip.Status.choices)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "trip status histories"

    def __str__(self):
        return f"{self.trip_id}: {self.status}"


def generate_delivery_otp() -> str:
    

    return f"{secrets.randbelow(10000):04d}"


class ProofOfDelivery(TimeStampedModel):
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name="proof_of_delivery")
    otp_code = models.CharField(max_length=4, default=generate_delivery_otp)
    otp_verified = models.BooleanField(default=False)
    photo = models.ImageField(upload_to="deliveries/proof/%Y/%m/", null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"ProofOfDelivery(trip={self.trip_id})"
