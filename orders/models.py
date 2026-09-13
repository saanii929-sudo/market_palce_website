import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Address
from cart.models import Coupon
from catalog.models import Product, ProductVariant
from core.models import TimeStampedModel


class DeliveryMethod(TimeStampedModel):
    name = models.CharField(max_length=50)
    code = models.SlugField(max_length=60, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    eta_days_min = models.PositiveSmallIntegerField()
    eta_days_max = models.PositiveSmallIntegerField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["price"]

    def __str__(self):
        return self.name


class PaymentMethod(TimeStampedModel):
    name = models.CharField(max_length=50)
    code = models.SlugField(max_length=60, unique=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


def generate_order_number(pk: int) -> str:
    return f"ST-{10000 + pk}"


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"

    ALLOWED_TRANSITIONS = {
        Status.PROCESSING: {Status.SHIPPED, Status.CANCELLED},
        Status.SHIPPED: {Status.OUT_FOR_DELIVERY, Status.CANCELLED},
        Status.OUT_FOR_DELIVERY: {Status.DELIVERED},
        Status.DELIVERED: set(),
        Status.CANCELLED: set(),
    }

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    order_number = models.CharField(max_length=20, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PROCESSING)

    # Lets a client retry a POST /orders/ safely (e.g. after a dropped
    # response) without double-placing the order. Optional: orders placed
    # without a key are never deduplicated against each other.
    idempotency_key = models.CharField(max_length=100, null=True, blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2)

    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
    delivery_method = models.ForeignKey(DeliveryMethod, on_delete=models.PROTECT, related_name="orders")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="orders")

    delivery_address = models.ForeignKey(
        Address, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders"
    )
    delivery_recipient_name = models.CharField(max_length=150)
    delivery_phone = models.CharField(max_length=20)
    delivery_line1 = models.CharField(max_length=255)
    delivery_line2 = models.CharField(max_length=255, blank=True)
    delivery_city = models.CharField(max_length=100)
    delivery_region = models.CharField(max_length=100, blank=True)
    delivery_country = models.CharField(max_length=100)

    placed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-placed_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_order_idempotency_key_per_user",
            )
        ]

    def __str__(self):
        return self.order_number or f"Order #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.order_number:
            self.order_number = generate_order_number(self.pk)
            super().save(update_fields=["order_number"])

    def snapshot_address(self, address: Address) -> None:
        self.delivery_address = address
        self.delivery_recipient_name = address.recipient_name
        self.delivery_phone = address.phone
        self.delivery_line1 = address.line1
        self.delivery_line2 = address.line2
        self.delivery_city = address.city
        self.delivery_region = address.region
        self.delivery_country = address.country

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: str, note: str = "") -> "OrderStatusHistory":
        if not self.can_transition_to(new_status):
            raise ValueError(f"Cannot move order from {self.status} to {new_status}.")

        self.status = new_status
        self.save(update_fields=["status", "updated_at"])
        history = OrderStatusHistory.objects.create(order=self, status=new_status, note=note)

        from .notifications import notify_order_status_change

        notify_order_status_change(self)

        return history


class OrderItem(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="order_items")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="order_items"
    )
    qty = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.qty} x {self.product.name} @ {self.unit_price}"

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.qty


class OrderStatusHistory(TimeStampedModel):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "order status histories"

    def __str__(self):
        return f"{self.order.order_number}: {self.status}"


class Shipment(TimeStampedModel):
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name="shipment")
    courier_name = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    current_status = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Shipment for {self.order.order_number}"


class Payment(TimeStampedModel):
    class Gateway(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"
        CASH_ON_DELIVERY = "cash_on_delivery", "Cash on delivery"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"
        REFUNDED = "refunded", "Refunded"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="payments")
    gateway = models.CharField(max_length=20, choices=Gateway.choices)
    gateway_reference = models.CharField(max_length=100, unique=True, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    amount = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Payment({self.order.order_number}, {self.gateway}, {self.status})"


def generate_payment_reference() -> str:
    return f"PAY-{secrets.token_hex(8).upper()}"


class ReturnRequest(TimeStampedModel):
    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="return_requests")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="return_requests")
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    requested_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    seller_note = models.CharField(max_length=255, blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Return for {self.order.order_number} ({self.status})"


class ReturnRequestItem(TimeStampedModel):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="return_items")
    qty = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.qty} x {self.order_item.product.name}"
