from django.contrib import admin

from .models import Delivery, DeliveryOffer, ProofOfDelivery, Trip, TripStatusHistory


class DeliveryOfferInline(admin.TabularInline):
    model = DeliveryOffer
    extra = 0
    readonly_fields = ["rider", "sent_at", "expires_at", "status"]
    can_delete = False


class TripStatusHistoryInline(admin.TabularInline):
    model = TripStatusHistory
    extra = 0
    readonly_fields = ["status", "note", "created_at"]
    can_delete = False


class ProofOfDeliveryInline(admin.StackedInline):
    model = ProofOfDelivery
    extra = 0
    readonly_fields = ["otp_code", "otp_verified", "photo", "delivered_at"]
    can_delete = False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ["id", "delivery_type", "status", "dispatch_attempts", "price", "distance_km", "created_at"]
    list_filter = ["delivery_type", "status"]
    search_fields = ["pickup_address", "dropoff_address", "pickup_contact_name", "dropoff_contact_name"]
    readonly_fields = ["content_type", "object_id", "dispatch_attempts"]
    inlines = [DeliveryOfferInline]


@admin.register(DeliveryOffer)
class DeliveryOfferAdmin(admin.ModelAdmin):
    list_display = ["id", "delivery", "rider", "status", "sent_at", "expires_at"]
    list_filter = ["status"]
    autocomplete_fields = ["delivery", "rider"]


@admin.register(Trip)
class TripAdmin(admin.ModelAdmin):
    list_display = ["id", "delivery", "rider", "status", "picked_up_at", "delivered_at"]
    list_filter = ["status"]
    search_fields = ["id", "rider__user__email"]
    autocomplete_fields = ["delivery", "rider"]
    inlines = [TripStatusHistoryInline, ProofOfDeliveryInline]
