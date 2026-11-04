from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async

KITCHEN_GROUP = "kitchen"


class KitchenConsumer(AsyncJsonWebsocketConsumer):
    """
    Every kitchen dashboard tab connects here. Views broadcast events
    (new_order, order_updated, order_removed) to the "kitchen" group via
    the channel layer; this consumer just forwards them straight to the
    browser as JSON. No per-order polling anywhere.
    """

    async def connect(self):
        await self.channel_layer.group_add(KITCHEN_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(KITCHEN_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # The only inbound message we need: a fresh snapshot after a
        # reconnect, in case events were missed while offline.
        if content.get("action") == "request_snapshot":
            orders = await self._get_active_orders()
            await self.send_json({"event": "snapshot", "orders": orders})

    @database_sync_to_async
    def _get_active_orders(self):
        # Local import to avoid any app-loading order issues at startup.
        from .views import serialize_order
        from .models import Order

        orders = Order.objects.filter(
            status__in=["new", "cooking", "awaiting_confirmation", "ready"]
        ).order_by("-id")
        return [serialize_order(o) for o in orders]

    # ---- group event handlers (type == "kitchen.event") ----
    async def kitchen_event(self, event):
        await self.send_json(event["data"])