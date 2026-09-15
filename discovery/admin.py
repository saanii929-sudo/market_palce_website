from django.contrib import admin

from .models import NewsletterSubscriber, RecentlyViewed, SearchQuery


@admin.register(RecentlyViewed)
class RecentlyViewedAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "session_key", "product", "viewed_at"]
    autocomplete_fields = ["user", "product"]


@admin.register(SearchQuery)
class SearchQueryAdmin(admin.ModelAdmin):
    list_display = ["id", "query_text", "user", "created_at"]
    search_fields = ["query_text"]
    autocomplete_fields = ["user"]


@admin.register(NewsletterSubscriber)
class NewsletterSubscriberAdmin(admin.ModelAdmin):
    list_display = ["id", "email", "is_active", "created_at"]
    search_fields = ["email"]
