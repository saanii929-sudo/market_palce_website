from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from catalog.models import Seller
from chat.models import Conversation
from chat.serializers import MessageSerializer
from chat.services import (
    ChatError,
    mark_read,
    post_message,
    start_customer_seller_conversation,
    start_customer_support_conversation,
    start_seller_support_conversation,
    unread_count,
    user_can_access_conversation,
)

from .views import _orders_count, seller_required


def _display_title(conversation, viewer):
    seller = getattr(viewer, "seller_profile", None)
    if conversation.kind == Conversation.Kind.CUSTOMER_SELLER:
        if seller and seller.id == conversation.seller_id:
            return conversation.customer.full_name or conversation.customer.email or "Customer"
        return conversation.seller.business_name

    if viewer.is_staff or viewer.is_superuser:
        if conversation.kind == Conversation.Kind.CUSTOMER_SUPPORT:
            return conversation.customer.full_name or conversation.customer.email or "Customer"
        return conversation.seller.business_name

    return "SportTech Support"


def _conversation_row(conversation, viewer):
    last = conversation.messages.order_by("-created_at").first()
    return {
        "id": conversation.id,
        "display_title": _display_title(conversation, viewer),
        "preview": (last.body[:60] if last else ""),
        "last_message_at": conversation.last_message_at,
        "unread": unread_count(conversation, viewer),
    }


def _render_chat_panel(request, template_name, active_nav_ctx, base_path):
    user = request.user
    seller = getattr(user, "seller_profile", None)

    if user.is_staff or user.is_superuser:
        conversations = Conversation.objects.filter(
            kind__in=[Conversation.Kind.CUSTOMER_SUPPORT, Conversation.Kind.SELLER_SUPPORT]
        ).select_related("customer", "seller")
    else:
        query = Q(customer=user)
        if seller is not None:
            query |= Q(seller=seller)
        conversations = Conversation.objects.filter(query).select_related("customer", "seller")

    conversation_rows = [_conversation_row(c, user) for c in conversations]

    active_conversation = None
    thread_messages = []
    active_title = ""
    ws_path = ""
    conversation_id = request.GET.get("conversation")
    if conversation_id:
        active_conversation = Conversation.objects.filter(id=conversation_id).select_related("customer", "seller").first()
        if active_conversation is None or not user_can_access_conversation(user, active_conversation):
            active_conversation = None
        else:
            thread_messages = active_conversation.messages.select_related("sender")
            active_title = _display_title(active_conversation, user)
            ws_path = f"/ws/chat/{active_conversation.id}/"
            mark_read(active_conversation, user)

    ctx = {
        **active_nav_ctx,
        "conversations": conversation_rows,
        "active_conversation": active_conversation,
        "active_title": active_title,
        "thread_messages": thread_messages,
        "ws_path": ws_path,
        "current_user_id": user.id,
        "base_path": base_path,
        "send_url": reverse("web-messages-send", args=[active_conversation.id]) if active_conversation else "",
    }
    return render(request, template_name, ctx)


@login_required(login_url="web-login")
def account_messages_view(request):
    return _render_chat_panel(
        request, "web/account_messages.html",
        {"active_nav": "messages", "orders_count": _orders_count(request)},
        reverse("web-account-messages"),
    )


@seller_required
def seller_messages_view(request, seller):
    from .views import _seller_order_qs
    from catalog.models import Product

    return _render_chat_panel(
        request, "web/seller_messages.html",
        {
            "active_nav": "messages", "seller": seller,
            "products_count": Product.objects.filter(seller=seller).count(),
            "orders_count": _seller_order_qs(seller).count(),
        },
        reverse("web-seller-messages"),
    )


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def messages_send_view(request, conversation_id):
    conversation = get_object_or_404(Conversation, id=conversation_id)
    if not user_can_access_conversation(request.user, conversation):
        return JsonResponse({"detail": "You don't have access to this conversation."}, status=403)

    import json

    try:
        body = json.loads(request.body or b"{}").get("body", "")
    except json.JSONDecodeError:
        body = request.POST.get("body", "")

    try:
        message = post_message(conversation, request.user, body)
    except ChatError as exc:
        return JsonResponse({"detail": exc.message}, status=400)

    return JsonResponse(MessageSerializer(message).data)


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def messages_start_seller_view(request, slug):
    seller = get_object_or_404(Seller, slug=slug)
    try:
        conversation = start_customer_seller_conversation(request.user, seller)
    except ChatError as exc:
        messages.error(request, exc.message)
        return redirect("web-seller-detail", slug=slug)
    return redirect(f"{reverse('web-account-messages')}?conversation={conversation.id}")


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def messages_start_support_view(request):
    as_seller = request.POST.get("as") == "seller" and getattr(request.user, "seller_profile", None) is not None
    if as_seller:
        conversation = start_seller_support_conversation(request.user)
        target = reverse("web-seller-messages")
    else:
        conversation = start_customer_support_conversation(request.user)
        target = reverse("web-account-messages")
    return redirect(f"{target}?conversation={conversation.id}")
