from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class ParcelTrackingConsumer(AsyncJsonWebsocketConsumer):
    """
    Customer-facing real-time tracking for a single parcel.

    Connect:
        wss://<host>/ws/parcels/<parcel_id>/tracking/?token=<JWT>

    The consumer:
      1. Verifies the connecting user owns the parcel (or is staff).
      2. Joins the group  parcel_tracking_<parcel_id>.
      3. Sends the full tracking snapshot immediately on connect so the
         client has something to render without waiting for the first push.
      4. Receives pushed events from two server-side sources:
           - rider location pings  (riders.views.RiderLocationPingView)
           - trip status changes   (deliveries.services — accept, pickup,
                                    complete, cancel)
         Both fire group_send(..., {"type": "tracking.update", ...}) to this
         group, which Channels routes to the tracking_update() handler below.

    Messages sent to the client always have the shape:
        {
          "type": "tracking_update",
          "data": <build_tracking_payload() result>
        }

    The client never sends any messages over this socket.

    Group name:   parcel_tracking_<parcel_id>
    Auth:         JWT via ?token= query param (JWTAuthMiddleware in asgi.py)
    Close codes:  4001 unauthenticated  |  4003 not owner / not found
    """

    async def connect(self):
        self.parcel_id = self.scope["url_route"]["kwargs"]["parcel_id"]
        user = self.scope.get("user")

        if not user or not user.is_authenticated:
            import logging
            logging.getLogger(__name__).warning(
                "ParcelTrackingConsumer: unauthenticated connection for parcel %s", self.parcel_id
            )
            await self.close(code=4001)
            return

        # Resolve delivery + verify ownership in one DB hit
        result = await database_sync_to_async(self._get_delivery)(user)
        if result is None:
            import logging
            logging.getLogger(__name__).warning(
                "ParcelTrackingConsumer: rejected user %s for parcel %s (not owner or not found)",
                user.id, self.parcel_id,
            )
            await self.close(code=4003)
            return

        self.delivery = result
        self.group_name = f"parcel_tracking_{self.parcel_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Push the current snapshot immediately so the client renders right
        # away rather than waiting for the first rider location ping.
        snapshot = await database_sync_to_async(self._get_snapshot)()
        await self.send_json({"type": "tracking_update", "data": snapshot})

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    # ------------------------------------------------------------------ #
    # Channel layer event handler                                          #
    # ------------------------------------------------------------------ #

    async def tracking_update(self, event):
        """Receives {"type": "tracking.update", "data": <payload>} from any
        group_send call targeting parcel_tracking_<parcel_id>, then forwards
        it to the WebSocket client.

        Note: Channels maps the dot-separated event type "tracking.update"
        to the underscore method name "tracking_update"."""
        await self.send_json({"type": "tracking_update", "data": event["data"]})

    # ------------------------------------------------------------------ #
    # Synchronous helpers (run via database_sync_to_async)                #
    # ------------------------------------------------------------------ #

    def _get_delivery(self, user):
        """Return the Delivery for this parcel if:
          - the user is the parcel sender, OR
          - the user is the rider currently assigned to this delivery, OR
          - the user is staff.
        Returns None otherwise."""
        from deliveries.models import Trip
        from parcels.models import Parcel
        from parcels.services import get_delivery_for_parcel

        try:
            parcel = Parcel.objects.get(pk=self.parcel_id)
        except Parcel.DoesNotExist:
            return None

        self.parcel = parcel
        delivery = get_delivery_for_parcel(parcel)

        if user.is_staff:
            return delivery

        if parcel.sender_id == user.id:
            return delivery

        # Allow the assigned rider to connect too (rider app tracking screen)
        if delivery is not None:
            rider_profile = getattr(user, "rider_profile", None)
            if rider_profile is not None:
                is_assigned = Trip.objects.filter(
                    delivery=delivery,
                    rider=rider_profile,
                    status__in=Trip.ACTIVE_STATUSES,
                ).exists()
                if is_assigned:
                    return delivery

        return None

    def _get_snapshot(self):
        from deliveries.services import build_tracking_payload

        # Re-fetch to get the freshest DB state.
        self.delivery.refresh_from_db()
        return build_tracking_payload(self.delivery)
