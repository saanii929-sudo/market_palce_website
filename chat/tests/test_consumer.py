import pytest
from channels.testing import WebsocketCommunicator

from accounts.tests.factories import UserFactory
from catalog.tests.factories import SellerFactory

from ..consumers import ChatConsumer
from ..models import Message
from ..services import start_customer_seller_conversation


def _communicator(conversation, user):
    communicator = WebsocketCommunicator(ChatConsumer.as_asgi(), f"/ws/chat/{conversation.id}/")
    communicator.scope["user"] = user
    communicator.scope["url_route"] = {"kwargs": {"conversation_id": str(conversation.id)}}
    return communicator


@pytest.mark.django_db(transaction=True)
async def test_consumer_rejects_unauthenticated_connection():
    from django.contrib.auth.models import AnonymousUser

    customer = await _sync(UserFactory)()
    seller = await _sync(SellerFactory)()
    conversation = await _sync(start_customer_seller_conversation)(customer, seller)

    communicator = _communicator(conversation, AnonymousUser())
    connected, _ = await communicator.connect()
    assert connected is False


@pytest.mark.django_db(transaction=True)
async def test_consumer_rejects_non_participant():
    customer = await _sync(UserFactory)()
    seller = await _sync(SellerFactory)()
    outsider = await _sync(UserFactory)()
    conversation = await _sync(start_customer_seller_conversation)(customer, seller)

    communicator = _communicator(conversation, outsider)
    connected, _ = await communicator.connect()
    assert connected is False


@pytest.mark.django_db(transaction=True)
async def test_consumer_persists_and_broadcasts_message():
    customer = await _sync(UserFactory)()
    seller_user = await _sync(UserFactory)()
    seller = await _sync(SellerFactory)(user=seller_user)
    conversation = await _sync(start_customer_seller_conversation)(customer, seller)

    customer_ws = _communicator(conversation, customer)
    seller_ws = _communicator(conversation, seller_user)
    assert (await customer_ws.connect())[0] is True
    assert (await seller_ws.connect())[0] is True

    await customer_ws.send_json_to({"body": "Hello, is this available?"})

    seller_event = await seller_ws.receive_json_from(timeout=5)
    assert seller_event["type"] == "message"
    assert seller_event["message"]["body"] == "Hello, is this available?"

    # The sender also receives their own message back (multi-device consistency).
    customer_event = await customer_ws.receive_json_from(timeout=5)
    assert customer_event["message"]["body"] == "Hello, is this available?"

    exists = await _sync(Message.objects.filter(conversation=conversation, body="Hello, is this available?").exists)()
    assert exists is True

    await customer_ws.disconnect()
    await seller_ws.disconnect()


@pytest.mark.django_db(transaction=True)
async def test_consumer_rejects_empty_message():
    customer = await _sync(UserFactory)()
    seller = await _sync(SellerFactory)()
    conversation = await _sync(start_customer_seller_conversation)(customer, seller)

    communicator = _communicator(conversation, customer)
    assert (await communicator.connect())[0] is True

    await communicator.send_json_to({"body": "   "})
    response = await communicator.receive_json_from(timeout=5)
    assert response["type"] == "error"

    await communicator.disconnect()


def _sync(fn):
    from asgiref.sync import sync_to_async

    return sync_to_async(fn)
