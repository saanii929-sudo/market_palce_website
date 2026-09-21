from django.contrib import admin

from .models import Review, ReviewFlag


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ["id", "product", "user", "rating", "status", "flagged_count", "created_at"]
    list_filter = ["rating", "status"]
    autocomplete_fields = ["product", "user", "order_item"]
    search_fields = ["product__name", "user__email"]
    readonly_fields = ["flagged_count"]


@admin.register(ReviewFlag)
class ReviewFlagAdmin(admin.ModelAdmin):
    list_display = ["id", "review", "flagged_by", "reason", "created_at"]
    list_filter = ["reason"]
    autocomplete_fields = ["review", "flagged_by"]
