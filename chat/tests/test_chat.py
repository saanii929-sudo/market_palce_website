import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import SellerFactory

from ..models import Conversation, Message
from ..services import (
    ChatError,
    mark_read,
    post_message,
    start_customer_seller_conversation,
    start_customer_support_conversation,
    start_seller_support_conversation,
    unread_count,
    user_can_access_conversation,
)


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_start_customer_seller_conversation_is_idempotent():
    customer = UserFactory()
    seller = SellerFactory()

    first = start_customer_seller_conversation(customer, seller)
    second = start_customer_seller_conversation(customer, seller)

    assert first.id == second.id
    assert Conversation.objects.filter(kind=Conversation.Kind.CUSTOMER_SELLER).count() == 1


@pytest.mark.django_db
def test_seller_cannot_message_their_own_store():
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)

    with pytest.raises(ChatError):
        start_customer_seller_conversation(seller_user, seller)


@pytest.mark.django_db
def test_customer_support_conversation_is_one_per_customer():
    customer = UserFactory()
    first = start_customer_support_conversation(customer)
    second = start_customer_support_conversation(customer)
    assert first.id == second.id


@pytest.mark.django_db
def test_seller_support_conversation_requires_seller_profile():
    user = UserFactory()
    with pytest.raises(ChatError):
        start_seller_support_conversation(user)


@pytest.mark.django_db
def test_only_participants_can_access_customer_seller_conversation():
    customer = UserFactory()
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)
    outsider = UserFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    assert user_can_access_conversation(customer, conversation)
    assert user_can_access_conversation(seller_user, conversation)
    assert not user_can_access_conversation(outsider, conversation)


@pytest.mark.django_db
def test_staff_can_access_any_support_conversation_but_not_customer_seller():
    staff = UserFactory(is_staff=True)
    customer = UserFactory()
    seller = SellerFactory()

    support_convo = start_customer_support_conversation(customer)
    cs_convo = start_customer_seller_conversation(customer, seller)

    assert user_can_access_conversation(staff, support_convo)
    assert not user_can_access_conversation(staff, cs_convo)


@pytest.mark.django_db
def test_post_message_persists_and_updates_last_message_at():
    customer = UserFactory()
    seller = SellerFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    message = post_message(conversation, customer, "Is this in stock?")

    assert Message.objects.filter(conversation=conversation, body="Is this in stock?").exists()
    conversation.refresh_from_db()
    assert conversation.last_message_at == message.created_at


@pytest.mark.django_db
def test_post_message_rejects_empty_body():
    customer = UserFactory()
    seller = SellerFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    with pytest.raises(ChatError):
        post_message(conversation, customer, "   ")


@pytest.mark.django_db
def test_post_message_rejects_non_participant():
    customer = UserFactory()
    seller = SellerFactory()
    outsider = UserFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    with pytest.raises(ChatError):
        post_message(conversation, outsider, "Hi")


@pytest.mark.django_db
def test_unread_count_and_mark_read():
    customer = UserFactory()
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)
    conversation = start_customer_seller_conversation(customer, seller)

    post_message(conversation, customer, "Hello")
    post_message(conversation, customer, "Are you there?")

    assert unread_count(conversation, seller_user) == 2
    assert unread_count(conversation, customer) == 0

    mark_read(conversation, seller_user)
    assert unread_count(conversation, seller_user) == 0


@pytest.mark.django_db
def test_conversation_start_api_creates_customer_seller_conversation():
    customer = UserFactory()
    seller = SellerFactory(slug="my-store")
    client = authed_client(customer)

    response = client.post(reverse("chat-conversation-start"), {"kind": "customer_seller", "seller_slug": "my-store"})
    assert response.status_code == 200
    assert response.data["kind"] == "customer_seller"
    assert response.data["seller"]["slug"] == "my-store"


@pytest.mark.django_db
def test_conversation_message_list_create_api():
    customer = UserFactory()
    seller = SellerFactory()
    conversation = start_customer_seller_conversation(customer, seller)
    client = authed_client(customer)

    response = client.post(
        reverse("chat-message-list", kwargs={"conversation_id": conversation.id}), {"body": "Do you ship to Kumasi?"}
    )
    assert response.status_code == 201
    assert response.data["body"] == "Do you ship to Kumasi?"

    listing = client.get(reverse("chat-message-list", kwargs={"conversation_id": conversation.id}))
    assert listing.status_code == 200
    assert len(listing.data["results"]) == 1


@pytest.mark.django_db
def test_conversation_message_list_denies_non_participants():
    customer = UserFactory()
    seller = SellerFactory()
    outsider = UserFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    response = authed_client(outsider).get(reverse("chat-message-list", kwargs={"conversation_id": conversation.id}))
    assert response.status_code == 403


@pytest.mark.django_db
def test_conversation_list_api_includes_customer_and_seller_threads():
    customer = UserFactory()
    seller = SellerFactory()
    start_customer_seller_conversation(customer, seller)
    start_customer_support_conversation(customer)

    response = authed_client(customer).get(reverse("chat-conversation-list"))
    assert response.status_code == 200
    assert len(response.data["results"]) == 2


@pytest.mark.django_db
def test_staff_conversation_list_only_shows_support_threads():
    staff = UserFactory(is_staff=True)
    customer = UserFactory()
    seller = SellerFactory()
    start_customer_seller_conversation(customer, seller)
    start_customer_support_conversation(customer)

    response = authed_client(staff).get(reverse("chat-conversation-list"))
    assert response.status_code == 200
    kinds = {c["kind"] for c in response.data["results"]}
    assert kinds == {"customer_support"}


@pytest.mark.django_db
def test_mark_read_api():
    customer = UserFactory()
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)
    conversation = start_customer_seller_conversation(customer, seller)
    post_message(conversation, customer, "Hi")

    response = authed_client(seller_user).post(reverse("chat-conversation-read", kwargs={"conversation_id": conversation.id}))
    assert response.status_code == 204
    conversation.refresh_from_db()
    assert unread_count(conversation, seller_user) == 0
