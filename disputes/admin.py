from django.contrib import admin

from .models import Dispute, DisputeMessage


class DisputeMessageInline(admin.TabularInline):
    model = DisputeMessage
    extra = 0
    readonly_fields = ["sender", "message", "attachments", "created_at"]
    can_delete = False


@admin.register(Dispute)
class DisputeAdmin(admin.ModelAdmin):
    list_display = ["id", "order", "category", "status", "raised_by", "against", "assigned_admin", "created_at"]
    list_filter = ["status", "category"]
    search_fields = ["order__order_number", "raised_by__email", "raised_by__phone"]
    autocomplete_fields = ["order", "refund_request", "raised_by", "against", "assigned_admin"]
    readonly_fields = ["created_at"]
    inlines = [DisputeMessageInline]
