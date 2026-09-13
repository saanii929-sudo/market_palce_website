from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from catalog.models import Product, ProductVariant
from core.models import TimeStampedModel


class Coupon(TimeStampedModel):
    class DiscountType(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    code = models.CharField(max_length=40, unique=True)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    min_order_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    max_discount_amount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    valid_from = models.DateTimeField(null=True, blank=True)
    valid_to = models.DateTimeField(null=True, blank=True)
    usage_limit = models.PositiveIntegerField(null=True, blank=True)
    times_used = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.code

    def validate_for_subtotal(self, subtotal: Decimal) -> str | None:
        """Returns an error message if the coupon can't be applied, else None."""
        now = timezone.now()
        if not self.is_active:
            return "This coupon is no longer active."
        if self.valid_from and now < self.valid_from:
            return "This coupon isn't active yet."
        if self.valid_to and now > self.valid_to:
            return "This coupon has expired."
        if self.usage_limit is not None and self.times_used >= self.usage_limit:
            return "This coupon has reached its usage limit."
        if self.min_order_amount is not None and subtotal < self.min_order_amount:
            return f"Minimum order amount for this coupon is {self.min_order_amount}."
        return None

    def compute_discount(self, subtotal: Decimal) -> Decimal:
        if self.discount_type == self.DiscountType.PERCENTAGE:
            discount = subtotal * (self.value / Decimal("100"))
        else:
            discount = self.value

        discount = min(discount, subtotal)
        if self.max_discount_amount is not None:
            discount = min(discount, self.max_discount_amount)
        return discount.quantize(Decimal("0.01"))


class Cart(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, null=True, blank=True, related_name="cart"
    )
    session_key = models.CharField(max_length=40, null=True, blank=True, unique=True)
    applied_coupon = models.ForeignKey(Coupon, on_delete=models.SET_NULL, null=True, blank=True, related_name="carts")

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(user__isnull=False) | models.Q(session_key__isnull=False),
                name="cart_must_have_user_or_session",
            )
        ]

    def __str__(self):
        return f"Cart({self.user or self.session_key})"


class CartItem(TimeStampedModel):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="cart_items")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.CASCADE, null=True, blank=True, related_name="cart_items"
    )
    qty = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("cart", "product", "variant")

    def __str__(self):
        return f"{self.qty} x {self.product.name}"

    @property
    def line_total(self) -> Decimal:
        return self.product.price * self.qty
