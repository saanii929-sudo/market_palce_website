from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone

from .models import Conversation, Message


class ChatError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _seller_of(user):
    return getattr(user, "seller_profile", None)


def user_can_access_conversation(user, conversation: Conversation) -> bool:
    if not user.is_authenticated:
        return False

    if conversation.kind == Conversation.Kind.CUSTOMER_SELLER:
        seller = _seller_of(user)
        return conversation.customer_id == user.id or bool(seller and seller.id == conversation.seller_id)

    # Support threads are a shared inbox - any staff member may access any of them.
    if user.is_staff or user.is_superuser:
        return True

    if conversation.kind == Conversation.Kind.CUSTOMER_SUPPORT:
        return conversation.customer_id == user.id

    if conversation.kind == Conversation.Kind.SELLER_SUPPORT:
        seller = _seller_of(user)
        return bool(seller and seller.id == conversation.seller_id)

    return False


def start_customer_seller_conversation(customer, seller) -> Conversation:
    if _seller_of(customer) is not None and _seller_of(customer).id == seller.id:
        raise ChatError("You can't message your own store.")

    conversation, _ = Conversation.objects.get_or_create(
        kind=Conversation.Kind.CUSTOMER_SELLER, customer=customer, seller=seller,
    )
    return conversation


def start_customer_support_conversation(customer) -> Conversation:
    conversation, _ = Conversation.objects.get_or_create(
        kind=Conversation.Kind.CUSTOMER_SUPPORT, customer=customer,
    )
    return conversation


def start_seller_support_conversation(user) -> Conversation:
    seller = _seller_of(user)
    if seller is None:
        raise ChatError("You don't have a seller account.")

    conversation, _ = Conversation.objects.get_or_create(
        kind=Conversation.Kind.SELLER_SUPPORT, seller=seller,
    )
    return conversation


def _broadcast(conversation_id: int, message: Message) -> None:
    from .serializers import MessageSerializer

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        f"chat_{conversation_id}",
        {"type": "chat.message", "message": MessageSerializer(message).data},
    )


def post_message(conversation: Conversation, sender, body: str) -> Message:
    body = (body or "").strip()
    if not body:
        raise ChatError("Message can't be empty.")
    if not user_can_access_conversation(sender, conversation):
        raise ChatError("You don't have access to this conversation.")

    message = Message.objects.create(conversation=conversation, sender=sender, body=body)
    Conversation.objects.filter(id=conversation.id).update(last_message_at=message.created_at)

    _notify_recipients(conversation, sender, body)
    _broadcast(conversation.id, message)
    return message


def _notify_recipients(conversation: Conversation, sender, body: str) -> None:
    from notifications.services import notify

    preview = body if len(body) <= 80 else body[:77] + "..."
    sender_seller = _seller_of(sender)
    title = "New message"

    if conversation.kind == Conversation.Kind.CUSTOMER_SELLER:
        if sender_seller and sender_seller.id == conversation.seller_id:
            if conversation.customer:
                notify(conversation.customer, "system", f"{sender_seller.business_name} sent you a message", preview)
        elif conversation.seller and conversation.seller.user_id:
            notify(conversation.seller.user, "system", title, preview)
        return

    if conversation.kind == Conversation.Kind.CUSTOMER_SUPPORT:
        if sender.id == conversation.customer_id:
            return  # customer messaged support - support has no single user to notify (shared inbox)
        if conversation.customer:
            notify(conversation.customer, "system", "Support sent you a message", preview)
        return

    if conversation.kind == Conversation.Kind.SELLER_SUPPORT:
        if sender_seller and sender_seller.id == conversation.seller_id:
            return  # seller messaged support - shared inbox, no single user to notify
        if conversation.seller and conversation.seller.user_id:
            notify(conversation.seller.user, "system", "Support sent you a message", preview)


def mark_read(conversation: Conversation, user) -> None:
    now = timezone.now()
    seller = _seller_of(user)

    if conversation.kind == Conversation.Kind.CUSTOMER_SELLER:
        if conversation.customer_id == user.id:
            conversation.customer_last_read_at = now
            conversation.save(update_fields=["customer_last_read_at"])
        elif seller and seller.id == conversation.seller_id:
            conversation.seller_last_read_at = now
            conversation.save(update_fields=["seller_last_read_at"])
        return

    if user.is_staff or user.is_superuser:
        conversation.support_last_read_at = now
        conversation.save(update_fields=["support_last_read_at"])
        return

    if conversation.kind == Conversation.Kind.CUSTOMER_SUPPORT and conversation.customer_id == user.id:
        conversation.customer_last_read_at = now
        conversation.save(update_fields=["customer_last_read_at"])
    elif conversation.kind == Conversation.Kind.SELLER_SUPPORT and seller and seller.id == conversation.seller_id:
        conversation.seller_last_read_at = now
        conversation.save(update_fields=["seller_last_read_at"])


def unread_count(conversation: Conversation, user) -> int:
    seller = _seller_of(user)

    if conversation.kind == Conversation.Kind.CUSTOMER_SELLER and conversation.customer_id == user.id:
        last_read = conversation.customer_last_read_at
    elif user.is_staff or user.is_superuser:
        last_read = conversation.support_last_read_at
    elif conversation.kind == Conversation.Kind.CUSTOMER_SUPPORT and conversation.customer_id == user.id:
        last_read = conversation.customer_last_read_at
    elif seller and seller.id == conversation.seller_id:
        last_read = conversation.seller_last_read_at
    else:
        return 0

    qs = conversation.messages.exclude(sender=user)
    if last_read:
        qs = qs.filter(created_at__gt=last_read)
    return qs.count()
