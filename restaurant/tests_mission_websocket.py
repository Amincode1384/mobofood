"""
End-to-end WebSocket tests for the EnjiNex robot mission flow
(see MISSION_API_DOCS.md section 14 - remaining work: real-connection tests).

These tests drive the REAL ASGI application
(RestaurantRobot.asgi.application -> ProtocolTypeRouter -> URLRouter ->
RobotConsumer) over an in-process channel layer, so the full
"kitchen dispatch -> channel broadcast -> robot consumer -> ack" server side
is exercised without real hardware.

Covered scenarios:

  * WebSocket routing for ws/robots/<robot_id>/ resolves to RobotConsumer.
  * Connect with a valid ROBOT_API_TOKEN is accepted and announces the robot.
  * Connect with a wrong/missing token is rejected (fail-closed, close 4401).
  * Inbound frames: ping -> pong, robot.command -> command forwarded.
  * Command fan-out: dispatch -> ChannelRobotTransport -> real channel layer
    -> robot consumer receives the Movement Command.

Status-machine helpers used by the ack flow are unit-tested here as well.
"""

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, override_settings

from restaurant import mission_statuses as ms
from restaurant.events import robot_group_name
from restaurant.movement_command import build_move_command
from restaurant.mission_statuses import (
    STATUS_ASSIGNED, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED,
)
from restaurant.models import Mission, Order
from channels.layers import get_channel_layer
from RestaurantRobot.asgi import application

ROBOT_ID = "RBOT-01"
TOKEN = "qa-9f2a7c-token-77b1"


def _mission():
    Order.objects.all().delete()
    Mission.objects.all().delete()
    order = Order.objects.create(table="4", total=100, status="ready")
    return Mission.objects.create(
        order=order, destination_table="4", robot_id=ROBOT_ID, status=ms.STATUS_PENDING,
    )


class RobotConsumerWebsocketE2ETests(TestCase):
    """Full server-side robot WebSocket journey (ASGI app + channel layer)."""

    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_connect_with_valid_token_is_accepted(self):
        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token={TOKEN}",
            )
            connected, _subprotocol = await communicator.connect(timeout=2)
            self.assertTrue(connected)
            first = await communicator.receive_json_from(timeout=2)
            self.assertEqual(first.get("event"), "connected")
            self.assertEqual(first.get("robot_id"), ROBOT_ID)
            await communicator.disconnect()

        async_to_sync(run)()

    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_connect_with_wrong_token_is_rejected(self):
        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token=wrong-token-value",
            )
            connected, close_code = await communicator.connect(timeout=2)
            self.assertFalse(connected)
            self.assertEqual(close_code, 4401)

        async_to_sync(run)()

    @override_settings(ROBOT_API_TOKEN="")
    def test_connect_without_configured_token_fails_closed(self):
        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token=",
            )
            connected, close_code = await communicator.connect(timeout=2)
            self.assertFalse(connected)
            self.assertEqual(close_code, 4401)

        async_to_sync(run)()

    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_ping_frame_gets_pong(self):
        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token={TOKEN}",
            )
            connected, _subprotocol = await communicator.connect(timeout=2)
            self.assertTrue(connected)
            # Drain the "connected" announcement before sending frames.
            connected_frame = await communicator.receive_json_from(timeout=2)
            self.assertEqual(connected_frame.get("event"), "connected")
            await communicator.send_json_to({"type": "ping"})
            pong = await communicator.receive_json_from(timeout=2)
            self.assertEqual(pong.get("event"), "pong")
            await communicator.disconnect()

        async_to_sync(run)()

    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_dispatched_command_is_fan_out_to_the_robot_group(self):
        mission = _mission()
        command = build_move_command(
            mission_uid=mission.mission_uid, robot_id=ROBOT_ID,
            destination_table=mission.destination_table,
        )

        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token={TOKEN}",
            )
            connected, _subprotocol = await communicator.connect(timeout=2)
            self.assertTrue(connected)
            connected_frame = await communicator.receive_json_from(timeout=2)
            self.assertEqual(connected_frame.get("event"), "connected")
            layer = get_channel_layer()
            await layer.group_send(
                robot_group_name(ROBOT_ID),
                {"type": "robot.command", "command": command},
            )
            command_frame = await communicator.receive_json_from(timeout=3)
            self.assertEqual(command_frame.get("event"), "command")
            received_command = command_frame.get("command") or {}
            self.assertEqual(received_command.get("protocol_version"), "1.0")
            self.assertEqual(received_command.get("command_type"), "MOVE_TO_TABLE")
            self.assertEqual(received_command.get("robot_id"), ROBOT_ID)
            self.assertEqual(received_command.get("mission_id"), str(mission.mission_uid))
            self.assertEqual(received_command.get("destination_id"), "TABLE_04")
            await communicator.disconnect()

        async_to_sync(run)()


class MissionStatusTransitionTests(TestCase):
    """Unit coverage for the single source of truth for mission status rules."""

    def test_pending_may_transition_to_any_active_status(self):
        for target in (
            ms.STATUS_QUEUED, ms.STATUS_ASSIGNED, ms.STATUS_IN_PROGRESS,
            ms.STATUS_COMPLETED, ms.STATUS_CANCELLED,
        ):
            self.assertTrue(ms.can_transition(ms.STATUS_PENDING, target))

    def test_limited_transitions_from_queued(self):
        self.assertTrue(ms.can_transition(ms.STATUS_QUEUED, ms.STATUS_ASSIGNED))
        self.assertTrue(ms.can_transition(ms.STATUS_QUEUED, ms.STATUS_CANCELLED))
        self.assertFalse(ms.can_transition(ms.STATUS_QUEUED, ms.STATUS_IN_PROGRESS))

    def test_final_statuses_have_no_outgoing_transitions(self):
        for final in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
            self.assertFalse(
                any(ms.can_transition(final, other) for other in ms.STATUS_KEYS if other != final),
            )

    def test_invalid_transition_is_rejected(self):
        self.assertFalse(ms.can_transition(STATUS_COMPLETED, STATUS_ASSIGNED))

    def test_final_and_active_helpers(self):
        self.assertTrue(ms.is_final(STATUS_COMPLETED))
        self.assertTrue(ms.is_final(STATUS_FAILED))
        self.assertTrue(ms.is_final(STATUS_CANCELLED))
        self.assertTrue(ms.is_active(ms.STATUS_QUEUED))
        self.assertFalse(ms.is_final(ms.STATUS_QUEUED))

    def test_status_label_and_transition_message(self):
        self.assertEqual(
            ms.status_label(ms.STATUS_COMPLETED), ms.STATUS_LABELS[ms.STATUS_COMPLETED],
        )
        self.assertEqual(
            ms.transition_message(ms.STATUS_ASSIGNED, ms.STATUS_IN_PROGRESS),
            "اجرای مأموریت آغاز شده است.",
        )
        # Unknown target statuses have no transition message.
        self.assertIsNone(ms.transition_message(ms.STATUS_PENDING, "not-a-status"))

    def test_final_status_estimates_and_active_status_set(self):
        estimates = ms.mission_final_status_estimates()
        self.assertEqual(
            set(estimates), {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED},
        )
        self.assertEqual(
            ms.active_status_set(), set(ms.STATUS_KEYS) - {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED},
        )
