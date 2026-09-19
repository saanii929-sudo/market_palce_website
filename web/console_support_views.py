from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from support.models import SupportContact

from .console_views import _base_ctx
from .messages_views import _render_chat_panel
from .views import _safe_redirect_target, superadmin_required


@superadmin_required
def console_messages_view(request):
    ctx = _base_ctx("messages")
    return _render_chat_panel(request, "web/console_messages.html", ctx, reverse("web-console-messages"))


# -- Support contacts -------------------------------------------------------

def _save_contact_from_form(request, contact=None):
    label = request.POST.get("label", "").strip()
    value = request.POST.get("value", "").strip()
    kind = request.POST.get("kind", "").strip()

    if not label or not value or kind not in SupportContact.Kind.values:
        messages.error(request, "Label, value, and a valid kind are all required.")
        return None

    if contact is None:
        contact = SupportContact()

    contact.label = label
    contact.value = value
    contact.kind = kind
    contact.display_order = int(request.POST.get("display_order") or 0)
    contact.is_active = bool(request.POST.get("is_active", "1"))
    contact.save()
    return contact


@superadmin_required
def console_support_contacts_view(request):
    ctx = _base_ctx("support-contacts")
    ctx["contacts"] = SupportContact.objects.all()
    return render(request, "web/console_support_contacts.html", ctx)


@superadmin_required
def console_support_contact_add_view(request):
    if request.method == "POST":
        contact = _save_contact_from_form(request)
        if contact is not None:
            messages.success(request, f'"{contact.label}" was added.')
            return redirect("web-console-support-contacts")

    ctx = _base_ctx("support-contacts")
    ctx["contact"] = None
    ctx["kinds"] = SupportContact.Kind.choices
    return render(request, "web/console_support_contact_form.html", ctx)


@superadmin_required
def console_support_contact_edit_view(request, contact_id):
    contact = get_object_or_404(SupportContact, id=contact_id)
    if request.method == "POST":
        saved = _save_contact_from_form(request, contact=contact)
        if saved is not None:
            messages.success(request, f'"{saved.label}" was updated.')
            return redirect("web-console-support-contacts")

    ctx = _base_ctx("support-contacts")
    ctx["contact"] = contact
    ctx["kinds"] = SupportContact.Kind.choices
    return render(request, "web/console_support_contact_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_support_contact_toggle_view(request, contact_id):
    contact = get_object_or_404(SupportContact, id=contact_id)
    contact.is_active = not contact.is_active
    contact.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-support-contacts")))


@superadmin_required
@require_http_methods(["POST"])
def console_support_contact_delete_view(request, contact_id):
    contact = get_object_or_404(SupportContact, id=contact_id)
    contact.delete()
    messages.success(request, f'"{contact.label}" was deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-support-contacts")))
