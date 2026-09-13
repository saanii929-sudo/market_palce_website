from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Address, OTPCode, User, UserInterest


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["-date_joined"]
    list_display = ["id", "email", "phone", "full_name", "role", "is_email_verified", "is_phone_verified", "is_active"]
    list_filter = ["role", "is_email_verified", "is_phone_verified", "is_active"]
    search_fields = ["email", "phone", "full_name"]
    readonly_fields = ["date_joined", "last_login", "created_at", "updated_at"]

    fieldsets = (
        (None, {"fields": ("email", "phone", "password")}),
        ("Profile", {"fields": ("full_name", "avatar", "bio", "role")}),
        (
            "Verification",
            {"fields": ("is_email_verified", "is_phone_verified")},
        ),
        (
            "Preferences",
            {"fields": ("push_notifications_enabled", "email_offers_enabled")},
        ),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("email", "phone", "password1", "password2"),
            },
        ),
    )


@admin.register(UserInterest)
class UserInterestAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "category"]
    autocomplete_fields = ["user", "category"]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "recipient_name", "city", "country", "is_default"]
    search_fields = ["recipient_name", "user__email", "user__phone"]
    autocomplete_fields = ["user"]


@admin.register(OTPCode)
class OTPCodeAdmin(admin.ModelAdmin):
    list_display = ["id", "destination", "channel", "purpose", "is_used", "attempts", "expires_at", "created_at"]
    list_filter = ["channel", "purpose", "is_used"]
    search_fields = ["destination"]
    readonly_fields = [f.name for f in OTPCode._meta.fields]
