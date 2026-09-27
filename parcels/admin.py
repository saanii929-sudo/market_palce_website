from django.contrib import admin
from django.utils.html import format_html

from .models import PackageSizePricing, Parcel


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ["id", "sender", "recipient_name", "package_size", "status", "price", "created_at"]
    list_filter = ["package_size", "status"]
    search_fields = ["sender__email", "recipient_name", "recipient_phone"]
    autocomplete_fields = ["sender", "pickup_address"]
    readonly_fields = ["price"]


@admin.register(PackageSizePricing)
class PackageSizePricingAdmin(admin.ModelAdmin):
    list_display = [
        "size", "label", "pricing_summary", "is_active", "updated_at",
    ]
    list_filter = ["size", "is_active"]
    list_editable = ["is_active"]
    search_fields = ["label", "description"]
    ordering = ["size", "-updated_at"]
    fieldsets = (
        (None, {
            "fields": ("size", "label", "description", "icon_url", "is_active"),
        }),
        ("Pricing", {
            "description": (
                "Set <strong>Flat fee</strong> for sizes charged a fixed amount regardless of distance "
                "(e.g. Document). For distance-based sizes set <strong>Base fee</strong> and "
                "<strong>Per-km rate</strong> and leave Flat fee blank."
            ),
            "fields": ("flat_fee", "base_fee", "per_km_rate"),
        }),
    )

    @admin.display(description="Pricing")
    def pricing_summary(self, obj):
        if obj.flat_fee is not None:
            return format_html("Flat &nbsp;<strong>GH₵{}</strong>", obj.flat_fee)
        base = obj.base_fee or "–"
        rate = obj.per_km_rate or "–"
        return format_html("GH₵{} base &nbsp;+&nbsp; GH₵{}/km", base, rate)
