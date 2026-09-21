from django.contrib import admin

from .models import Broadcast, DeviceToken, Notification
from .tasks import send_broadcast


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "type", "title", "is_read", "created_at"]
    list_filter = ["type", "is_read"]
    search_fields = ["title", "user__email"]
    autocomplete_fields = ["user"]


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "platform", "is_active", "last_seen_at"]
    list_filter = ["platform", "is_active"]
    search_fields = ["user__email", "user__phone", "token"]
    autocomplete_fields = ["user"]
    readonly_fields = ["token"]


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ["id", "title", "audience", "scheduled_for", "sent_at", "created_by", "created_at"]
    list_filter = ["audience"]
    search_fields = ["title"]
    autocomplete_fields = ["created_by"]
    readonly_fields = ["sent_at"]
    actions = ["send_selected"]

    @admin.action(description="Send selected broadcasts now")
    def send_selected(self, request, queryset):
        sent = 0
        for broadcast in queryset.filter(sent_at__isnull=True):
            send_broadcast.delay(broadcast.id)
            sent += 1
        if sent:
            self.message_user(request, f"Queued {sent} broadcast(s) to send.")
