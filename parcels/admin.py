from django.contrib import admin

from .models import Parcel


@admin.register(Parcel)
class ParcelAdmin(admin.ModelAdmin):
    list_display = ["id", "sender", "recipient_name", "package_size", "status", "price", "created_at"]
    list_filter = ["package_size", "status"]
    search_fields = ["sender__email", "recipient_name", "recipient_phone"]
    autocomplete_fields = ["sender", "pickup_address"]
    readonly_fields = ["price"]
