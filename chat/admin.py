from django.contrib import admin

from .models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ["sender", "body", "created_at"]
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "customer", "seller", "last_message_at", "created_at"]
    list_filter = ["kind"]
    search_fields = ["customer__email", "seller__business_name"]
    autocomplete_fields = ["customer", "seller"]
    inlines = [MessageInline]
