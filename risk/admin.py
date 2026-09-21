from django.contrib import admin

from .models import RiskFlag


@admin.register(RiskFlag)
class RiskFlagAdmin(admin.ModelAdmin):
    list_display = ["id", "flag_type", "score", "content_type", "object_id", "reviewed", "created_at"]
    list_filter = ["flag_type", "reviewed"]
    readonly_fields = ["content_type", "object_id", "flag_type", "score", "details", "created_at"]
    autocomplete_fields = ["reviewed_by"]
