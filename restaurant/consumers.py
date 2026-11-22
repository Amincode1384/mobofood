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
        # فقط کارکنان واردشده می‌توانند به کانال زندهٔ آشپزخانه وصل شوند؛
        # پیام‌های این کانال شامل نام و تلفن مشتری است.
        user = self.scope.get("user")
        if user is None or not user.is_authenticated or not user.is_staff:
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(KITCHEN_GROUP, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(KITCHEN_GROUP, self.channel_name)

    async def receive_json(self, content, **kwargs):
        try:
            await self._handle_inbound(content, **kwargs)
        except Exception as exc:
            # ثبت خطا در SystemErrorLog؛ رفتار قبلی (بالا رفتن استثنا)
            # بدون تغییر حفظ می‌شود. fail-open.
            await self._record_error(f"{type(exc).__name__}: {exc}")
            raise

    async def _handle_inbound(self, content, **kwargs):
        # The only inbound message we need: a fresh snapshot after a
        # reconnect, in case events were missed while offline.
        if content.get("action") == "request_snapshot":
            orders = await self._get_active_orders()
            missions = await self._get_active_missions()
            await self.send_json(
                {"event": "snapshot", "orders": orders, "missions": missions}
            )

    @database_sync_to_async
    def _record_error(self, message):
        """ثبت خطای کانال آشپزخانه در SystemErrorLog (fail-open)."""
        from .error_logs import record_system_error

        record_system_error(
            level="ERROR",
            source="websocket",
            code="kitchen_receive_failed",
            message=message,
            path="ws/kitchen",
        )

    @database_sync_to_async
    def _get_active_orders(self):
        # Local import to avoid any app-loading order issues at startup.
        from .views import serialize_order
        from .models import Order

        orders = Order.objects.filter(
            status__in=["new", "cooking", "awaiting_confirmation", "ready"]
        ).order_by("-id")
        return [serialize_order(o) for o in orders]

    @database_sync_to_async
    def _get_active_missions(self):
        from .models import Mission

        missions = Mission.objects.filter(
            status__in=["pending", "assigned", "in_progress"]
        ).order_by("-created_at")
        return [
            {
                "mission_code": m.mission_code,
                "mission_uid": str(m.mission_uid),
                "status": m.status,
                "destination_table": m.destination_table,
                "robot_id": m.robot_id,
                "order_id": m.order_id,
                "order_table": m.order.table,
                "created_at": m.created_at.isoformat(),
                "started_at": m.started_at.isoformat() if m.started_at else None,
                "completed_at": m.completed_at.isoformat() if m.completed_at else None,
            }
            for m in missions
        ]

    # ---- group event handlers (type == "kitchen.event") ----
    async def kitchen_event(self, event):
        await self.send_json(event["data"])