from django.contrib import admin

from .models import FAQ, SupportContact, SupportTicket


@admin.register(FAQ)
class FAQAdmin(admin.ModelAdmin):
    list_display = ["id", "question", "topic", "display_order", "is_active"]
    list_filter = ["topic", "is_active"]
    search_fields = ["question", "answer"]


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "channel", "subject", "status", "created_at"]
    list_filter = ["channel", "status"]
    search_fields = ["subject", "user__email"]
    autocomplete_fields = ["user"]


@admin.register(SupportContact)
class SupportContactAdmin(admin.ModelAdmin):
    list_display = ["label", "kind", "value", "is_active", "display_order"]
    list_filter = ["kind", "is_active"]
