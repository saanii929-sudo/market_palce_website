from django.contrib import admin, messages
from django.utils.html import format_html

from .models import Payout, SellerApplication
from .services import SellerApplicationError, approve_application, reject_application


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ["seller", "amount", "method", "status", "payout_date"]
    list_filter = ["status", "seller"]


@admin.register(SellerApplication)
class SellerApplicationAdmin(admin.ModelAdmin):
    list_display = [
        "id", "business_name", "user", "category", "phone", "documents_uploaded",
        "status", "submitted_at", "reviewed_at",
    ]
    list_filter = ["status", "category"]
    search_fields = ["business_name", "user__email", "user__phone"]
    autocomplete_fields = ["user", "category"]
    # status/reviewer_note are only ever changed through the approve/reject
    # actions below, so a decision always goes through submit_application's
    # side effects (promoting the user to seller, creating the Seller row,
    # sending the notification) instead of silently drifting out of sync.
    readonly_fields = [
        "submitted_at", "reviewed_at", "status", "reviewer_note", "document_links",
    ]
    fields = [
        "user", "business_name", "category", "phone",
        "id_document", "business_certificate", "document_links",
        "status", "reviewer_note", "submitted_at", "reviewed_at",
    ]
    actions = ["approve_applications", "reject_applications"]

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
