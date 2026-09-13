from django.contrib import admin

from .models import WishlistItem


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "session_key", "product", "created_at"]
    search_fields = ["user__email", "session_key", "product__name"]
    autocomplete_fields = ["user", "product"]
