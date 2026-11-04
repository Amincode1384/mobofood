from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

KITCHEN_GROUP = "kitchen"


def broadcast_order_event(event_name, order_data):
    """
    Push {"event": event_name, "order": order_data} to every connected
    kitchen dashboard. Called from views (sync) and the timer watcher
    thread (also sync) — async_to_sync bridges into the channel layer.
    """
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return  # CHANNEL_LAYERS not configured — fail silently in dev/tests
    async_to_sync(channel_layer.group_send)(
        KITCHEN_GROUP,
        {
            "type": "kitchen.event",
            "data": {"event": event_name, "order": order_data},
        },
    )


def broadcast_order_removed(order_id):
    channel_layer = get_channel_layer()
    if channel_layer is None:
        return
    async_to_sync(channel_layer.group_send)(
        KITCHEN_GROUP,
        {
            "type": "kitchen.event",
            "data": {"event": "order_removed", "order_id": order_id},
        },
    )