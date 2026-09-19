from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from .models import Conversation
from .serializers import MessageSerializer
from .services import ChatError, post_message, user_can_access_conversation


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """One WebSocket connection per open conversation. Send {"body": "..."}
    to post a message; every connected participant (including the sender,
    for multi-device consistency) receives {"type": "message", "message": {...}}.

    Messages sent over plain REST (ConversationMessageListCreateView) are
    broadcast through this same group by chat.services.post_message, so a
    client can freely mix WebSocket and REST without missing anything."""

    async def connect(self):
        self.conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        user = self.scope["user"]

        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        allowed = await database_sync_to_async(self._user_can_access)(user)
        if not allowed:
            await self.close(code=4003)
            return

        self.group_name = f"chat_{self.conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        body = content.get("body")
        try:
            await database_sync_to_async(self._post_message)(body)
        except ChatError as exc:
            await self.send_json({"type": "error", "detail": exc.message})

    async def chat_message(self, event):
        await self.send_json({"type": "message", "message": event["message"]})

    def _user_can_access(self, user):
        try:
            conversation = Conversation.objects.get(id=self.conversation_id)
        except Conversation.DoesNotExist:
            return False
        return user_can_access_conversation(user, conversation)

    def _post_message(self, body):
        conversation = Conversation.objects.get(id=self.conversation_id)
        message = post_message(conversation, self.scope["user"], body)
        return MessageSerializer(message).data
