from django.contrib import admin, messages

from .models import RiderDocument, RiderEarning, RiderPayout, RiderPayoutAccount, RiderProfile, RiderRating, Vehicle
from .services import RiderError, mark_rider_payout_paid, reject_rider_payout, review_document, schedule_rider_payout


class VehicleInline(admin.TabularInline):
    model = Vehicle
    extra = 0


class RiderDocumentInline(admin.TabularInline):
    model = RiderDocument
    extra = 0
    readonly_fields = ["doc_type", "file", "status", "reviewer_note", "reviewed_at"]
    can_delete = False


@admin.register(RiderProfile)
class RiderProfileAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "is_online", "is_verified", "rating_avg", "acceptance_rate", "created_at"]
    list_filter = ["is_online", "is_verified"]
    search_fields = ["user__email", "user__phone", "user__full_name"]
    autocomplete_fields = ["user"]
    readonly_fields = ["is_verified"]
    inlines = [VehicleInline, RiderDocumentInline]


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "type", "make", "model", "plate_number", "created_at"]
    list_filter = ["type"]
    search_fields = ["rider__user__email", "plate_number"]
    autocomplete_fields = ["rider"]


@admin.register(RiderDocument)
class RiderDocumentAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "doc_type", "status", "expires_at", "created_at"]
    list_filter = ["doc_type", "status"]
    search_fields = ["rider__user__email"]
    autocomplete_fields = ["rider"]
    actions = ["verify_selected", "reject_selected"]

    @admin.action(description="Verify selected documents")
    def verify_selected(self, request, queryset):
        verified = 0
        for document in queryset:
            try:
                review_document(document, action="verify", reviewer_note=f"Verified by {request.user}")
                verified += 1
            except RiderError as exc:
                self.message_user(request, f"{document}: {exc.message}", level=messages.WARNING)
        if verified:
            self.message_user(request, f"Verified {verified} document(s).", level=messages.SUCCESS)

    @admin.action(description="Reject selected documents")
    def reject_selected(self, request, queryset):
        rejected = 0
        for document in queryset:
            try:
                review_document(document, action="reject", reviewer_note=f"Rejected by {request.user}")
                rejected += 1
            except RiderError as exc:
                self.message_user(request, f"{document}: {exc.message}", level=messages.WARNING)
        if rejected:
            self.message_user(request, f"Rejected {rejected} document(s).", level=messages.SUCCESS)


@admin.register(RiderPayoutAccount)
class RiderPayoutAccountAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "type", "is_active", "created_at"]
    list_filter = ["type", "is_active"]
    search_fields = ["rider__user__email"]
    autocomplete_fields = ["rider"]
    readonly_fields = ["account_reference"]


@admin.register(RiderEarning)
class RiderEarningAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "trip", "base_fare", "distance_bonus", "tip", "surge_multiplier", "total", "created_at"]
    search_fields = ["rider__user__email"]
    autocomplete_fields = ["rider", "trip"]
    readonly_fields = ["trip", "rider", "base_fare", "distance_bonus", "tip", "surge_multiplier", "total"]


@admin.register(RiderPayout)
class RiderPayoutAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "amount", "status", "requested_at", "completed_at"]
    list_filter = ["status"]
    search_fields = ["rider__user__email"]
    autocomplete_fields = ["rider", "payout_account"]
    actions = ["schedule_selected", "mark_paid_selected", "reject_selected"]

    @admin.action(description="Schedule selected payouts")
    def schedule_selected(self, request, queryset):
        for payout in queryset:
            try:
                schedule_rider_payout(payout, admin_note=f"Scheduled by {request.user}")
            except RiderError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)

    @admin.action(description="Mark selected payouts as paid")
    def mark_paid_selected(self, request, queryset):
        for payout in queryset:
            try:
                mark_rider_payout_paid(payout)
            except RiderError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)

    @admin.action(description="Reject selected payouts")
    def reject_selected(self, request, queryset):
        for payout in queryset:
            try:
                reject_rider_payout(payout, admin_note=f"Rejected by {request.user}")
            except RiderError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)


@admin.register(RiderRating)
class RiderRatingAdmin(admin.ModelAdmin):
    list_display = ["id", "rider", "customer", "stars", "trip", "created_at"]
    list_filter = ["stars"]
    search_fields = ["rider__user__email", "customer__email"]
    autocomplete_fields = ["rider", "customer", "trip"]
    readonly_fields = ["trip", "customer", "rider", "stars", "comment"]
