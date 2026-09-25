from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class RiderDispatchConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope["user"]
        if not user or not user.is_authenticated:
            await self.close(code=4001)
            return

        rider_profile_id = await database_sync_to_async(self._rider_profile_id)(user)
        if rider_profile_id is None:
            await self.close(code=4003)
            return

        self.group_name = f"rider_dispatch_{rider_profile_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def delivery_request(self, event):
        await self.send_json({"type": "delivery_request", "request": event["request"]})

    def _rider_profile_id(self, user):
        rider_profile = getattr(user, "rider_profile", None)
        return rider_profile.id if rider_profile is not None else None
