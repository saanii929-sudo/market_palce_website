from django.contrib import admin

from .models import StaticPage


@admin.register(StaticPage)
class StaticPageAdmin(admin.ModelAdmin):
    list_display = ["id", "slug", "title", "updated_at"]
    search_fields = ["title", "slug"]
