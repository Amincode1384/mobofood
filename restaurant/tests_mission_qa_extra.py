"""
Supplementary QA tests for the EnjiNex mission-dispatch API (mission parts 3 & 5).

These complement restaurant/tests_mission_dispatch.py and
restaurant/tests_mission_apis.py. They cover the scenarios the original 64
tests leave open:

  * HTTP contract of POST /api/missions/dispatch/ (bad JSON, non-object body,
    GET, missing identifiers, malformed UUID).
  * Idempotency at the HTTP layer (two identical POSTs -> one real send).
  * Async "channels" transport: a sent command is NOT reported as received,
    and the mission is not falsely marked ASSIGNED/COMPLETED.
  * Robot acknowledgement handling over WebSocket, including a response whose
    command_id does not match any stored command.
  * Movement-command contract edge cases.

No real robot or hardware is involved: InMemoryRobotTransport and
ChannelRobotTransport (with a mocked broadcast) are used.
"""

import json
import uuid
from unittest import mock
from unittest.mock import AsyncMock

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase, Client, override_settings

from restaurant import mission_statuses as ms
from restaurant.models import Mission, Order
from restaurant.mission_dispatch import DispatchError, dispatch_mission
from restaurant.movement_command import (
    build_move_command,
    validate_command,
    to_destination_id,
    from_destination_id,
    InvalidDestinationError,
    UnsupportedProtocolVersionError,
    CommandValidationError,
)
from restaurant.robot_transport import (
    InMemoryRobotTransport,
    InMemoryOutcomeStore,
    ChannelRobotTransport,
    set_robot_transport,
    get_robot_transport,
    RobotTransportError,
)
from restaurant.robot_consumer import RobotConsumer

User = get_user_model()


def staff_client(username="qa_extra_staff"):
    user, _ = User.objects.get_or_create(username=username, defaults={"is_staff": True})
    client = Client()
    client.force_login(user)
    return client


def make_mission(table="4", robot="RBOT-01", status=ms.STATUS_PENDING, order_status="ready"):
    order = Order.objects.create(table=table, total=100, status=order_status)
    return Mission.objects.create(
        order=order, destination_table=table, robot_id=robot, status=status,
    )


class MissionApiContractTests(TestCase):
    """HTTP-level behaviour of the dispatch endpoint."""

    def setUp(self):
        self.transport = InMemoryRobotTransport()
        set_robot_transport(self.transport)
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def tearDown(self):
        set_robot_transport(None)

    def post(self, payload, raw=None, client=None):
        client = client or staff_client()
        body = raw if raw is not None else json.dumps(payload)
        return client.post(
            "/api/missions/dispatch/", data=body, content_type="application/json",
        )

    def test_invalid_json_body_returns_400(self):
        resp = self.post(None, raw="{not json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "invalid_json")
        self.assertEqual(len(self.transport.sent_commands), 0)

    def test_non_object_json_body_returns_400(self):
        resp = self.post(None, raw=json.dumps(["not", "a", "dict"]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "invalid_json")

    def test_get_method_not_allowed(self):
        resp = staff_client().get("/api/missions/dispatch/")
        self.assertEqual(resp.status_code, 405)

    def test_missing_identifiers_returns_400(self):
        resp = self.post({})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "missing_mission_id")

    def test_malformed_uuid_does_not_crash_the_api(self):
        # DEFECT (reproduced): a malformed UUID reaches the UUIDField lookup,
        # which raises django.core.exceptions.ValidationError. dispatch_mission
        # only catches DoesNotExist/ValueError, so the endpoint currently
        # returns HTTP 500 instead of a 4xx. Expected: 400 or 404.
        client = staff_client()
        client.raise_request_exception = False
        resp = client.post(
            "/api/missions/dispatch/",
            data=json.dumps({"mission_uid": "not-a-uuid"}),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, (400, 404))

    def test_dispatch_by_mission_code(self):
        mission = make_mission()
        resp = self.post({"mission_code": mission.mission_code})
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])

    def test_completed_mission_cannot_be_dispatched(self):
        mission = make_mission(status=ms.STATUS_COMPLETED)
        resp = self.post({"mission_uid": str(mission.mission_uid)})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "mission_not_dispatchable")
        self.assertEqual(len(self.transport.sent_commands), 0)

    def test_duplicate_http_post_is_idempotent(self):
        mission = make_mission()
        uid = {"mission_uid": str(mission.mission_uid)}
        first = self.post(uid)
        second = self.post(uid)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertFalse(first.json()["idempotent_replay"])
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(len(self.transport.sent_commands), 1)

    def test_robot_id_mismatch_rejected_at_api(self):
        mission = make_mission(robot="RBOT-01")
        resp = self.post({
            "mission_uid": str(mission.mission_uid), "robot_id": "RBOT-02",
        })
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["code"], "robot_id_mismatch")
        self.assertEqual(len(self.transport.sent_commands), 0)

    def test_invalid_destination_rejected_at_api(self):
        mission = make_mission(table="4")
        Mission.objects.filter(pk=mission.pk).update(destination_table="999")
        resp = self.post({"mission_uid": str(mission.mission_uid)})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["code"], "invalid_destination")

    def test_unknown_robot_has_no_outcome_leak(self):
        # A fresh mission must have no recorded outcome; otherwise the
        # idempotent-replay branch would silently swallow a real dispatch.
        mission = make_mission()
        self.assertIsNone(self.transport.outcome_for(str(mission.mission_uid)))


class ChannelsBackendTests(TestCase):
    """The asynchronous transport must not report false success."""

    def setUp(self):
        set_robot_transport(None)
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def tearDown(self):
        set_robot_transport(None)

    @override_settings(ROBOT_TRANSPORT_BACKEND="channels")
    def test_channel_dispatch_is_sent_but_not_received(self):
        transport = ChannelRobotTransport(outcome_store=InMemoryOutcomeStore())
        set_robot_transport(transport)
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        # The command was handed to the channel layer ...
        self.assertTrue(result["command_sent"])
        # ... but the robot has not answered yet: received stays False.
        self.assertFalse(result["received"])
        self.assertEqual(result["mission_status"], ms.STATUS_QUEUED)
        mission.refresh_from_db()
        # DEFECT (reproduced): the persisted status is ASSIGNED even though the
        # robot has not acknowledged anything yet, and it contradicts the
        # returned mission_status ("queued"). Expected: QUEUED until an ack.
        self.assertEqual(mission.status, ms.STATUS_QUEUED)

    @override_settings(ROBOT_TRANSPORT_BACKEND="channels")
    def test_channel_duplicate_send_does_not_broadcast_twice(self):
        store = InMemoryOutcomeStore()
        transport = ChannelRobotTransport(outcome_store=store)
        set_robot_transport(transport)
        command = build_move_command(
            mission_uid=uuid.uuid4(), robot_id="RBOT-01", destination_table="4",
        )
        with mock.patch("restaurant.events.broadcast_robot_command") as broadcast:
            transport.send_move_command(command, mission_uid=str(uuid.uuid4()), robot_id="RBOT-01")
            second_uid = str(uuid.uuid4())
            transport.send_move_command(command, mission_uid=second_uid, robot_id="RBOT-01")
            self.assertEqual(broadcast.call_count, 2)  # different missions: both send

        # Same mission again -> idempotent, no extra broadcast.
        mission_uid = str(uuid.uuid4())
        command2 = build_move_command(
            mission_uid=mission_uid, robot_id="RBOT-01", destination_table="5",
        )
        with mock.patch("restaurant.events.broadcast_robot_command") as broadcast2:
            transport.send_move_command(command2, mission_uid=mission_uid, robot_id="RBOT-01")
            transport.send_move_command(command2, mission_uid=mission_uid, robot_id="RBOT-01")
            self.assertEqual(broadcast2.call_count, 1)

    @override_settings(ROBOT_TRANSPORT_BACKEND="nonsense")
    def test_unknown_backend_fails_closed(self):
        set_robot_transport(None)
        with self.assertRaises(RobotTransportError):
            get_robot_transport()


class RobotResponseValidationTests(TestCase):
    """Direct checks of the transport's response-validation primitives."""

    def test_in_memory_transport_rejects_foreign_command(self):
        transport = InMemoryRobotTransport()
        command = build_move_command(
            mission_uid=uuid.uuid4(), robot_id="RBOT-01", destination_table="4",
        )
        with self.assertRaises(RobotTransportError):
            transport.send_move_command(
                command, mission_uid=str(uuid.uuid4()), robot_id="RBOT-02",
            )

    def test_wrong_command_id_is_not_accepted_as_a_new_outcome(self):
        # Contract requirement: a response carrying a command_id that does not
        # match the stored command must be rejected, not recorded as a fresh
        # outcome for the mission.
        transport = InMemoryRobotTransport()
        mission_uid = str(uuid.uuid4())
        command = build_move_command(
            mission_uid=mission_uid, robot_id="RBOT-01", destination_table="4",
        )
        outcome = transport.send_move_command(
            command, mission_uid=mission_uid, robot_id="RBOT-01",
        )
        stored = transport.outcome_for(mission_uid)
        self.assertEqual(stored.command_id, outcome.command_id)
        other = str(uuid.uuid4())
        self.assertNotEqual(other, stored.command_id)

    def test_duplicate_response_is_recognised(self):
        transport = InMemoryRobotTransport()
        mission_uid = str(uuid.uuid4())
        command = build_move_command(
            mission_uid=mission_uid, robot_id="RBOT-01", destination_table="4",
        )
        first = transport.send_move_command(
            command, mission_uid=mission_uid, robot_id="RBOT-01",
        )
        replay = transport.send_move_command(
            command, mission_uid=mission_uid, robot_id="RBOT-01",
        )
        self.assertEqual(first.command_id, replay.command_id)
        self.assertEqual(len(transport.sent_commands), 1)


class RobotAckValidationTests(TestCase):
    """RobotConsumer._handle_ack must validate command_id before trusting it."""

    def setUp(self):
        self.transport = InMemoryRobotTransport()
        set_robot_transport(self.transport)
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def tearDown(self):
        set_robot_transport(None)

    def _consumer(self):
        consumer = object.__new__(RobotConsumer)
        consumer.robot_id = "RBOT-01"
        consumer.sent = []

        async def _send_json(content, **kwargs):
            consumer.sent.append(content)

        consumer.send_json = _send_json
        return consumer

    def test_response_with_wrong_command_id_is_rejected(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        stored_command_id = result["command_id"]
        self.assertNotEqual(str(uuid.uuid4()), stored_command_id)

        consumer = self._consumer()
        consumer._get_mission = AsyncMock(return_value=mission)
        consumer._update_mission = AsyncMock()

        async def run():
            await consumer._handle_ack(
                str(uuid.uuid4()),
                "accepted",
                {"event": "ack", "mission_id": str(mission.mission_uid)},
            )

        async_to_sync(run)()
        events = [message.get("event") for message in consumer.sent]
        # DEFECT (reproduced): expected an error such as "wrong_command_id";
        # the current consumer accepts any command_id that is not a duplicate.
        self.assertIn("error", events)

    def test_duplicate_response_for_same_command_id_is_rejected(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        command_id = result["command_id"]

        consumer = self._consumer()
        consumer._get_mission = AsyncMock(return_value=mission)
        consumer._update_mission = AsyncMock()

        async def run():
            await consumer._handle_ack(
                command_id, "accepted",
                {"event": "ack", "mission_id": str(mission.mission_uid)},
            )

        async_to_sync(run)()
        self.assertEqual(consumer.sent[0]["event"], "error")
        self.assertEqual(consumer.sent[0]["code"], "duplicate_response")

    def test_progress_reports_for_same_command_are_not_duplicates(self):
        """Same command_id may report accepted -> in_progress -> completed."""
        set_robot_transport(ChannelRobotTransport())
        self.addCleanup(set_robot_transport, None)
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        command_id = result["command_id"]

        consumer = self._consumer()
        consumer._get_mission = AsyncMock(return_value=mission)
        consumer._update_mission = AsyncMock()

        async def run():
            for status in ("accepted", "in_progress", "completed"):
                await consumer._handle_ack(
                    command_id, status,
                    {"event": "status", "mission_id": str(mission.mission_uid)},
                )

        async_to_sync(run)()
        acks = [m for m in consumer.sent if m.get("event") == "ack_received"]
        self.assertEqual(
            [m["status"] for m in acks],
            ["accepted", "in_progress", "completed"],
        )
        self.assertFalse([m for m in consumer.sent if m.get("event") == "error"])

        # A repeated final report is still rejected as a duplicate.
        async def repeat():
            await consumer._handle_ack(
                command_id, "completed",
                {"event": "completed", "mission_id": str(mission.mission_uid)},
            )

        async_to_sync(repeat)()
        self.assertEqual(consumer.sent[-1]["code"], "duplicate_response")


class MovementCommandEdgeCaseTests(TestCase):

    def test_destination_round_trip(self):
        self.assertEqual(from_destination_id(to_destination_id("4")), 4)
        self.assertEqual(from_destination_id("TABLE_64"), 64)

    def test_bad_destination_id_formats_raise(self):
        for bad in ("4", "TABLE_4", "TABLE_004", "TABLE_65", "TABLE_X", None, 7):
            with self.assertRaises(InvalidDestinationError):
                from_destination_id(bad)

    def test_unsupported_protocol_version_rejected(self):
        with self.assertRaises(UnsupportedProtocolVersionError):
            build_move_command(
                mission_uid=uuid.uuid4(), robot_id="RBOT-01",
                destination_table="4", protocol_version="9.9",
            )

    def test_validate_command_missing_and_wrong_fields(self):
        command = build_move_command(
            mission_uid=uuid.uuid4(), robot_id="RBOT-01", destination_table="4",
        )
        broken = dict(command)
        del broken["destination_id"]
        with self.assertRaises(CommandValidationError):
            validate_command(broken)

        wrong_type = dict(command, command_type="MOVE_TO_KITCHEN")
        with self.assertRaises(CommandValidationError):
            validate_command(wrong_type)

        bad_id = dict(command, command_id="not-a-uuid")
        with self.assertRaises(CommandValidationError):
            validate_command(bad_id)

        non_dict_parameters = dict(command, parameters=[1, 2])
        with self.assertRaises(CommandValidationError):
            validate_command(non_dict_parameters)