import secrets
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Address
from cart.models import Coupon
from catalog.models import Category, Product, ProductVariant, Seller
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


class TaxRule(TimeStampedModel):
    region = models.CharField(max_length=100, help_text="e.g. 'Greater Accra' - matches Address.region.")
    category = models.ForeignKey(
        Category, on_delete=models.CASCADE, null=True, blank=True, related_name="tax_rules",
        help_text="Leave blank for a region-wide rate covering every category.",
    )
    rate = models.DecimalField(max_digits=6, decimal_places=4, help_text="e.g. 0.1250 for Ghana's 12.5% VAT.")
    effective_from = models.DateField(default=timezone.now)

    class Meta:
        ordering = ["-effective_from"]

    def __str__(self):
        scope = f"{self.region}/{self.category}" if self.category else self.region
        return f"{scope}: {self.rate}"

    @classmethod
    def get_rate(cls, region: str, category=None, as_of=None) -> Decimal:
        as_of = as_of or timezone.now().date()
        qs = cls.objects.filter(region=region, effective_from__lte=as_of)

        if category is not None:
            specific = qs.filter(category=category).order_by("-effective_from").first()
            if specific:
                return specific.rate

        general = qs.filter(category__isnull=True).order_by("-effective_from").first()
        return general.rate if general else Decimal("0.00")


class Order(TimeStampedModel):
    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        SHIPPED = "shipped", "Shipped"
        OUT_FOR_DELIVERY = "out_for_delivery", "Out for delivery"
        DELIVERED = "delivered", "Delivered"
        CANCELLED = "cancelled", "Cancelled"
        PARTIAL = "partial", "Partially delivered"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="orders")
    order_number = models.CharField(max_length=20, unique=True, blank=True)

    idempotency_key = models.CharField(max_length=100, null=True, blank=True)

    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="orders")
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

    @property
    def status(self) -> str:
        sub_orders = list(self.seller_orders.all())
        if not sub_orders:
            return self.Status.PROCESSING

        statuses = {so.status for so in sub_orders}
        if statuses == {self.Status.CANCELLED}:
            return self.Status.CANCELLED

        live_statuses = statuses - {self.Status.CANCELLED}
        if not live_statuses:
            return self.Status.CANCELLED
        if live_statuses == {self.Status.DELIVERED}:
            return self.Status.DELIVERED
        if len(live_statuses) == 1:
            return next(iter(live_statuses))
        return self.Status.PARTIAL

    def get_status_display(self) -> str:
        return self.Status(self.status).label

    @property
    def subtotal(self) -> Decimal:
        return self.seller_orders.aggregate(t=models.Sum("subtotal"))["t"] or Decimal("0.00")

    @property
    def tax_amount(self) -> Decimal:
        return self.seller_orders.aggregate(t=models.Sum("tax_amount"))["t"] or Decimal("0.00")

    @property
    def delivery_fee(self) -> Decimal:
        return self.seller_orders.aggregate(t=models.Sum("delivery_fee"))["t"] or Decimal("0.00")

    @property
    def discount_amount(self) -> Decimal:
        return self.seller_orders.aggregate(t=models.Sum("discount_amount"))["t"] or Decimal("0.00")

    @property
    def total(self) -> Decimal:
        return self.seller_orders.aggregate(t=models.Sum("total"))["t"] or Decimal("0.00")

    @property
    def items(self):
        """Every item across every seller in this order, flattened - for
        templates/call sites that just want "what did the customer buy"
        without caring which seller shipped which line (e.g. the small
        order-thumbnail icons shown in an order list row)."""
        return OrderItem.objects.filter(seller_order__order=self)


def generate_suborder_number(order_number: str, index: int) -> str:
    return f"{order_number}-{chr(65 + index)}"


class SellerOrder(TimeStampedModel):
    Status = Order.Status
    ALLOWED_TRANSITIONS = {
        Status.PROCESSING: {Status.SHIPPED, Status.CANCELLED},
        Status.SHIPPED: {Status.OUT_FOR_DELIVERY, Status.CANCELLED},
        Status.OUT_FOR_DELIVERY: {Status.DELIVERED},
        Status.DELIVERED: set(),
        Status.CANCELLED: set(),
        Status.PARTIAL: set(),  # never actually stored - Order-aggregate only
    }

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="seller_orders")
    seller = models.ForeignKey(Seller, on_delete=models.PROTECT, related_name="seller_orders")
    suborder_number = models.CharField(max_length=30, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=Order.Status.choices, default=Order.Status.PROCESSING)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2)

    delivery_method = models.ForeignKey(DeliveryMethod, on_delete=models.PROTECT, related_name="seller_orders")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.suborder_number or f"SellerOrder #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.suborder_number:
            index = self.order.seller_orders.exclude(pk=self.pk).count()
            self.suborder_number = generate_suborder_number(self.order.order_number, index)
            super().save(update_fields=["suborder_number"])

    @property
    def delivery_recipient_name(self):
        return self.order.delivery_recipient_name

    @property
    def delivery_phone(self):
        return self.order.delivery_phone

    @property
    def delivery_line1(self):
        return self.order.delivery_line1

    @property
    def delivery_line2(self):
        return self.order.delivery_line2

    @property
    def delivery_city(self):
        return self.order.delivery_city

    @property
    def delivery_region(self):
        return self.order.delivery_region

    @property
    def delivery_country(self):
        return self.order.delivery_country

    @property
    def order_number(self):
        return self.suborder_number

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: str, note: str = "") -> "OrderStatusHistory":
        if not self.can_transition_to(new_status):
            raise ValueError(f"Cannot move order from {self.status} to {new_status}.")

        self.status = new_status
        self.save(update_fields=["status", "updated_at"])
        history = OrderStatusHistory.objects.create(seller_order=self, status=new_status, note=note)

        shipment, _ = Shipment.objects.get_or_create(seller_order=self)
        shipment.current_status = self.get_status_display()
        shipment.save(update_fields=["current_status", "updated_at"])

        from .notifications import notify_seller_order_status_change

        notify_seller_order_status_change(self)

        return history

    def get_status_display(self) -> str:
        return self.Status(self.status).label


class OrderItem(TimeStampedModel):
    seller_order = models.ForeignKey(SellerOrder, on_delete=models.CASCADE, related_name="items")
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

    @property
    def order(self) -> Order:
        return self.seller_order.order


class OrderStatusHistory(TimeStampedModel):
    seller_order = models.ForeignKey(SellerOrder, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=20, choices=Order.Status.choices)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "order status histories"

    def __str__(self):
        return f"{self.seller_order.suborder_number}: {self.status}"


class Shipment(TimeStampedModel):
    seller_order = models.OneToOneField(SellerOrder, on_delete=models.CASCADE, related_name="shipment")
    courier_name = models.CharField(max_length=100, blank=True)
    tracking_number = models.CharField(max_length=100, blank=True)
    current_status = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"Shipment for {self.seller_order.suborder_number}"


class Payment(TimeStampedModel):
    class Gateway(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"
        HUBTEL = "hubtel", "Hubtel"
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

    seller_order = models.ForeignKey(SellerOrder, on_delete=models.CASCADE, related_name="return_requests")
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
        return f"Return for {self.seller_order.suborder_number} ({self.status})"

    @property
    def order(self) -> Order:
        return self.seller_order.order


class ReturnRequestItem(TimeStampedModel):
    return_request = models.ForeignKey(ReturnRequest, on_delete=models.CASCADE, related_name="items")
    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="return_items")
    qty = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.qty} x {self.order_item.product.name}"


class RefundRequest(TimeStampedModel):
    """A distinct, formal money-back workflow from ReturnRequest above -
    ReturnRequest is about the physical logistics of shipping an item back
    to a seller; this is about the state-machined decision of whether (and
    how much) money moves back to the buyer, independent of whether a
    physical return happens (e.g. "damaged" claims often don't need one)."""

    class Reason(models.TextChoices):
        NOT_AS_DESCRIBED = "not_as_described", "Not as described"
        DAMAGED = "damaged", "Damaged"
        WRONG_ITEM = "wrong_item", "Wrong item"
        CHANGED_MIND = "changed_mind", "Changed mind"
        OTHER = "other", "Other"

    class RefundType(models.TextChoices):
        REFUND = "refund", "Refund"
        EXCHANGE = "exchange", "Exchange"

    class Status(models.TextChoices):
        REQUESTED = "requested", "Requested"
        UNDER_REVIEW = "under_review", "Under review"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REFUNDED = "refunded", "Refunded"

    ALLOWED_TRANSITIONS = {
        Status.REQUESTED: {Status.UNDER_REVIEW},
        Status.UNDER_REVIEW: {Status.APPROVED, Status.REJECTED},
        Status.APPROVED: {Status.REFUNDED},
        Status.REJECTED: set(),
        Status.REFUNDED: set(),
    }

    order_item = models.ForeignKey(OrderItem, on_delete=models.CASCADE, related_name="refund_requests")
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="refund_requests")
    reason = models.CharField(max_length=20, choices=Reason.choices)
    reason_detail = models.TextField(blank=True)
    photos = models.JSONField(default=list, blank=True, help_text="List of uploaded photo URLs.")
    refund_type = models.CharField(max_length=20, choices=RefundType.choices, default=RefundType.REFUND)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    requested_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"Refund for {self.order_item} ({self.status})"

    @property
    def seller_order(self) -> "SellerOrder":
        return self.order_item.seller_order

    @property
    def is_escalated(self) -> bool:
        from disputes.models import Dispute

        return self.disputes.exclude(status__in=Dispute.RESOLVED_STATUSES).exists()

    def can_transition_to(self, new_status: str) -> bool:
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, set())

    def transition_to(self, new_status: str, *, actor=None, note: str = "") -> "RefundStatusHistory":
        if not self.can_transition_to(new_status):
            raise ValueError(f"Cannot move refund request from {self.status} to {new_status}.")
        return self._apply_transition(new_status, actor=actor, note=note)

    def admin_override_to(self, new_status: str, *, actor, note: str = "") -> "RefundStatusHistory":
        """Bypasses can_transition_to's forward-only check. The only caller
        is disputes.services.resolve_dispute: resolving a Dispute in the
        buyer's favor must be able to push a REJECTED refund request back to
        APPROVED (reopening a decision the seller already made), which the
        normal buyer/seller-facing state machine deliberately never allows."""
        return self._apply_transition(new_status, actor=actor, note=note)

    def _apply_transition(self, new_status: str, *, actor=None, note: str = "") -> "RefundStatusHistory":
        self.status = new_status
        update_fields = ["status", "updated_at"]
        if new_status in (self.Status.REJECTED, self.Status.REFUNDED):
            self.resolved_at = timezone.now()
            update_fields.append("resolved_at")
        if note:
            self.resolution_note = note
            update_fields.append("resolution_note")
        self.save(update_fields=update_fields)

        history = RefundStatusHistory.objects.create(refund_request=self, status=new_status, note=note, actor=actor)

        from .notifications import notify_refund_status_change

        notify_refund_status_change(self)

        return history

    def get_status_display(self) -> str:
        return self.Status(self.status).label


class RefundStatusHistory(TimeStampedModel):
    refund_request = models.ForeignKey(RefundRequest, on_delete=models.CASCADE, related_name="status_history")
    status = models.CharField(max_length=20, choices=RefundRequest.Status.choices)
    note = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="refund_status_changes"
    )

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "refund status histories"

    def __str__(self):
        return f"{self.refund_request_id}: {self.status}"


def generate_checkout_reference() -> str:
    return f"HBT-{secrets.token_hex(6).upper()}"


class PendingCheckout(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        PAID = "paid", "Paid"
        FAILED = "failed", "Failed"

    reference = models.CharField(max_length=40, unique=True, default=generate_checkout_reference)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pending_checkouts")
    address = models.ForeignKey(Address, on_delete=models.PROTECT, related_name="pending_checkouts")
    delivery_method = models.ForeignKey(DeliveryMethod, on_delete=models.PROTECT, related_name="pending_checkouts")
    payment_method = models.ForeignKey(PaymentMethod, on_delete=models.PROTECT, related_name="pending_checkouts")
    coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_checkouts")

    cart_snapshot = models.JSONField()

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2)

    idempotency_key = models.CharField(max_length=100, null=True, blank=True)
    checkout_url = models.URLField(max_length=500, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    failure_reason = models.CharField(max_length=255, blank=True)
    order = models.OneToOneField(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name="pending_checkout")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="unique_pending_checkout_idempotency_key_per_user",
            )
        ]

    def __str__(self):
        return f"PendingCheckout({self.reference}, {self.status})"
