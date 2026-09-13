from django.contrib import admin

from .models import PaymentMethodToken


@admin.register(PaymentMethodToken)
class PaymentMethodTokenAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "gateway", "brand", "last4", "is_default"]
    search_fields = ["user__email", "user__phone", "last4"]
    autocomplete_fields = ["user"]
