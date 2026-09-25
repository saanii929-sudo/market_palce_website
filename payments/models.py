from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class PaymentMethodToken(TimeStampedModel):

    class Gateway(models.TextChoices):
        PAYSTACK = "paystack", "Paystack"
        FLUTTERWAVE = "flutterwave", "Flutterwave"
        HUBTEL = "hubtel", "Hubtel"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payment_methods")
    gateway = models.CharField(max_length=20, choices=Gateway.choices)
    token = models.CharField(max_length=255)
    brand = models.CharField(max_length=30, blank=True, help_text="e.g. visa, mastercard, mtn_momo")
    last4 = models.CharField(max_length=4, blank=True)
    expiry_month = models.PositiveSmallIntegerField(null=True, blank=True)
    expiry_year = models.PositiveSmallIntegerField(null=True, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.brand or self.gateway} •••• {self.last4}".strip()

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if self.is_default:
            PaymentMethodToken.objects.filter(user=self.user).exclude(id=self.id).update(is_default=False)
        elif is_new:
            from core.defaults import assign_default_on_create

            assign_default_on_create(self)
