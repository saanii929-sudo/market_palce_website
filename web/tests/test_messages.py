import json

import pytest
from django.test import Client
from django.urls import reverse

from accounts.tests.factories import UserFactory
from catalog.tests.factories import SellerFactory
from chat.models import Conversation
from chat.services import (
    post_message,
    start_customer_seller_conversation,
    start_customer_support_conversation,
    start_seller_support_conversation,
)

from .test_catalog_management import admin_client, seller_client


def _client_for(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_message_seller_button_starts_a_conversation_and_redirects_to_thread():
    customer = UserFactory()
    seller = SellerFactory(slug="baseline-store")
    client = _client_for(customer)

    response = client.post(reverse("web-messages-start-seller", args=["baseline-store"]))
    assert response.status_code == 302
    conversation = Conversation.objects.get(kind=Conversation.Kind.CUSTOMER_SELLER, customer=customer, seller=seller)
    assert response.url == f"{reverse('web-account-messages')}?conversation={conversation.id}"


@pytest.mark.django_db
def test_seller_cannot_message_their_own_storefront():
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user, slug="own-store")
    client = _client_for(seller_user)

    response = client.post(reverse("web-messages-start-seller", args=["own-store"]))
    assert response.status_code == 302
    assert response.url == reverse("web-seller-detail", args=["own-store"])
    assert not Conversation.objects.exists()


@pytest.mark.django_db
def test_account_messages_page_shows_conversation_and_history():
    customer = UserFactory()
    seller = SellerFactory()
    conversation = start_customer_seller_conversation(customer, seller)

    client = _client_for(customer)
    response = client.get(reverse("web-account-messages"), {"conversation": conversation.id})
    assert response.status_code == 200
    assert seller.business_name.encode() in response.content


@pytest.mark.django_db
def test_seller_messages_page_requires_seller_account():
    client, _ = seller_client()
    response = client.get(reverse("web-seller-messages"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_console_messages_requires_superadmin():
    client, _ = admin_client()
    response = client.get(reverse("web-console-messages"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_non_admin_cannot_access_console_messages():
    customer = UserFactory()
    client = _client_for(customer)
    response = client.get(reverse("web-console-messages"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_messages_send_fallback_endpoint():
    customer = UserFactory()
    seller = SellerFactory()
    conversation = start_customer_seller_conversation(customer, seller)
    client = _client_for(customer)

    response = client.post(
        reverse("web-messages-send", args=[conversation.id]),
        data=json.dumps({"body": "Do you have size 42?"}),
        content_type="application/json",
    )
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data["body"] == "Do you have size 42?"


@pytest.mark.django_db
def test_start_support_conversation_as_customer():
    customer = UserFactory()
    client = _client_for(customer)

    response = client.post(reverse("web-messages-start-support"))
    assert response.status_code == 302
    conversation = Conversation.objects.get(kind=Conversation.Kind.CUSTOMER_SUPPORT, customer=customer)
    assert response.url == f"{reverse('web-account-messages')}?conversation={conversation.id}"


@pytest.mark.django_db
def test_start_support_conversation_as_seller():
    client, seller = seller_client()

    response = client.post(reverse("web-messages-start-support"), {"as": "seller"})
    assert response.status_code == 302
    conversation = Conversation.objects.get(kind=Conversation.Kind.SELLER_SUPPORT, seller=seller)
    assert response.url == f"{reverse('web-seller-messages')}?conversation={conversation.id}"


@pytest.mark.django_db
def test_console_can_manage_support_contacts():
    from support.models import SupportContact

    client, _ = admin_client()
    response = client.post(reverse("web-console-support-contact-add"), {
        "label": "General support", "kind": "email", "value": "help@sporttech.example",
        "display_order": "0", "is_active": "1",
    })
    assert response.status_code == 302
    contact = SupportContact.objects.get(label="General support")
    assert contact.value == "help@sporttech.example"

    toggle = client.post(reverse("web-console-support-contact-toggle", args=[contact.id]))
    assert toggle.status_code == 302
    contact.refresh_from_db()
    assert contact.is_active is False


@pytest.mark.django_db
def test_console_inbox_shows_seller_business_name_not_generic_support_label():
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user, business_name="Baseline Sports")
    conversation = start_seller_support_conversation(seller_user)
    post_message(conversation, seller_user, "I need help with a payout.")

    client, _ = admin_client()
    response = client.get(reverse("web-console-messages"), {"conversation": conversation.id})
    assert response.status_code == 200
    assert b"Baseline Sports" in response.content
    assert b"SportTech Support" not in response.content


@pytest.mark.django_db
def test_console_inbox_shows_customer_name_for_customer_support_thread():
    customer = UserFactory(full_name="Ama Shopper")
    conversation = start_customer_support_conversation(customer)
    post_message(conversation, customer, "Where is my order?")

    client, _ = admin_client()
    response = client.get(reverse("web-console-messages"), {"conversation": conversation.id})
    assert response.status_code == 200
    assert b"Ama Shopper" in response.content


@pytest.mark.django_db
def test_seller_inbox_shows_support_label_for_seller_support_thread():
    seller_client_, seller = seller_client()
    conversation = start_seller_support_conversation(seller.user)
    post_message(conversation, seller.user, "Hi, question about my payout")

    response = seller_client_.get(reverse("web-seller-messages"), {"conversation": conversation.id})
    assert response.status_code == 200
    assert b"SportTech Support" in response.content


@pytest.mark.django_db
def test_support_page_shows_chat_button_and_active_contacts():
    from support.models import SupportContact

    SupportContact.objects.create(kind="email", label="General", value="help@sporttech.example", is_active=True)
    customer = UserFactory()
    client = _client_for(customer)

    response = client.get(reverse("web-support"))
    assert response.status_code == 200
    assert b"Chat with support" in response.content
    assert b"help@sporttech.example" in response.content
