from django.contrib import admin

from .models import Review


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "user", "rating", "created_at"]
    list_filter = ["rating"]
    autocomplete_fields = ["product", "user", "order_item"]
    search_fields = ["product__name", "user__email"]
