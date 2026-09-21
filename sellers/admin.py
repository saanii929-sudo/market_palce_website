from django.contrib import admin, messages
from django.utils import timezone
from django.utils.html import format_html

from .models import BulkUploadJob, Payout, PayoutAccount, SellerApplication, SellerSubscription, SubscriptionPlan
from .services import (
    PayoutError,
    SellerApplicationError,
    approve_application,
    mark_payout_paid,
    reject_application,
    reject_kyc,
    reject_payout,
    schedule_payout,
    verify_kyc,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ["name", "price", "billing_period_days", "is_active", "is_featured", "display_order"]
    list_filter = ["is_active", "is_featured"]
    search_fields = ["name"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(SellerSubscription)
class SellerSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["seller", "plan", "amount", "status", "starts_at", "expires_at", "created_at"]
    list_filter = ["status", "plan"]
    search_fields = ["seller__business_name", "reference"]
    autocomplete_fields = ["seller", "plan"]
    readonly_fields = ["reference", "checkout_url", "failure_reason"]


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["seller", "amount", "method", "account_details", "status", "created_at", "payout_date"]
    list_filter = ["status", "seller"]
    autocomplete_fields = ["seller"]
    actions = ["schedule_selected", "mark_paid_selected", "reject_selected"]

    @admin.action(description="Schedule for payout today")
    def schedule_selected(self, request, queryset):
        scheduled = 0
        for payout in queryset:
            try:
                schedule_payout(payout, payout_date=timezone.now().date(), admin_note=f"Scheduled by {request.user}")
                scheduled += 1
            except PayoutError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)
        if scheduled:
            self.message_user(request, f"Scheduled {scheduled} payout(s).", level=messages.SUCCESS)

    @admin.action(description="Mark selected as paid")
    def mark_paid_selected(self, request, queryset):
        paid = 0
        for payout in queryset:
            try:
                mark_payout_paid(payout)
                paid += 1
            except PayoutError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)
        if paid:
            self.message_user(request, f"Marked {paid} payout(s) as paid.", level=messages.SUCCESS)

    @admin.action(description="Reject selected requests")
    def reject_selected(self, request, queryset):
        rejected = 0
        for payout in queryset:
            try:
                reject_payout(payout, admin_note=f"Rejected by {request.user}")
                rejected += 1
            except PayoutError as exc:
                self.message_user(request, f"{payout}: {exc.message}", level=messages.WARNING)
        if rejected:
            self.message_user(request, f"Rejected {rejected} payout(s).", level=messages.SUCCESS)


@admin.register(SellerApplication)
class SellerApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "id", "business_name", "user", "category", "phone", "documents_uploaded",
        "status", "submitted_at", "reviewed_at",
    ]
    list_filter = ["status", "category"]
    search_fields = ["business_name", "user__email", "user__phone"]
    autocomplete_fields = ["user", "category"]
    readonly_fields = [
        "submitted_at", "reviewed_at", "status", "reviewer_note", "document_links",
        "kyc_status", "kyc_reviewed_at",
    ]
    fields = [
        "user", "business_name", "category", "phone",
        "id_document", "business_certificate", "document_links",
        "status", "reviewer_note", "submitted_at", "reviewed_at",
        "bank_account_name", "bank_account_number", "bank_name", "momo_number", "momo_network",
        "kyc_status", "kyc_reviewed_at",
    ]
    actions = ["approve_applications", "reject_applications", "verify_kyc_selected", "reject_kyc_selected"]

    @admin.display(description="Documents")
    def documents_uploaded(self, obj):
        parts = []
        if obj.id_document:
            parts.append("ID")
        if obj.business_certificate:
            parts.append("Certificate")
        return ", ".join(parts) if parts else "—"

    @admin.display(description="Uploaded documents")
    def document_links(self, obj):
        links = []
        if obj.id_document:
            links.append(f'<a href="{obj.id_document.url}" target="_blank">View ID document</a>')
        if obj.business_certificate:
            links.append(f'<a href="{obj.business_certificate.url}" target="_blank">View business certificate</a>')
        if not links:
            return "No documents uploaded."
        return format_html("<br>".join(links))

    @admin.action(description="Approve selected applications")
    def approve_applications(self, request, queryset):
        approved = 0
        for application in queryset:
            try:
                approve_application(application, reviewer_note=f"Approved by {request.user}")
                approved += 1
            except SellerApplicationError as exc:
                self.message_user(request, f"{application}: {exc.message}", level=messages.WARNING)
        if approved:
            self.message_user(request, f"Approved {approved} application(s).", level=messages.SUCCESS)

    @admin.action(description="Reject selected applications")
    def reject_applications(self, request, queryset):
        rejected = 0
        for application in queryset:
            try:
                reject_application(application, reviewer_note=f"Rejected by {request.user}")
                rejected += 1
            except SellerApplicationError as exc:
                self.message_user(request, f"{application}: {exc.message}", level=messages.WARNING)
        if rejected:
            self.message_user(request, f"Rejected {rejected} application(s).", level=messages.SUCCESS)

    @admin.action(description="Verify KYC for selected")
    def verify_kyc_selected(self, request, queryset):
        verified = 0
        for application in queryset:
            try:
                verify_kyc(application)
                verified += 1
            except SellerApplicationError as exc:
                self.message_user(request, f"{application}: {exc.message}", level=messages.WARNING)
        if verified:
            self.message_user(request, f"Verified KYC for {verified} application(s).", level=messages.SUCCESS)

    @admin.action(description="Reject KYC for selected")
    def reject_kyc_selected(self, request, queryset):
        rejected = 0
        for application in queryset:
            try:
                reject_kyc(application, reviewer_note=f"KYC rejected by {request.user}")
                rejected += 1
            except SellerApplicationError as exc:
                self.message_user(request, f"{application}: {exc.message}", level=messages.WARNING)
        if rejected:
            self.message_user(request, f"Rejected KYC for {rejected} application(s).", level=messages.SUCCESS)


@admin.register(PayoutAccount)
class PayoutAccountAdmin(admin.ModelAdmin):
    list_display = ["seller", "type", "is_active", "created_at"]
    list_filter = ["type", "is_active"]
    search_fields = ["seller__business_name"]
    autocomplete_fields = ["seller"]
    readonly_fields = ["account_reference"]


@admin.register(BulkUploadJob)
class BulkUploadJobAdmin(admin.ModelAdmin):
    list_display = ["id", "seller", "status", "total_rows", "success_count", "error_count", "created_at"]
    list_filter = ["status"]
    search_fields = ["seller__business_name"]
    autocomplete_fields = ["seller"]
    readonly_fields = ["status", "total_rows", "success_count", "error_count", "error_report"]
