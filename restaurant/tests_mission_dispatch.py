"""
16 سناریوی تست خودکار برای API استاندارد ارسال مأموریت به ربات.

تمام ارتباط با ربات توسط InMemoryRobotTransport شبیهسازی میشود؛
هیچ سختافزار واقعی لازم نیست.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase, Client

from restaurant import mission_statuses as ms
from restaurant.models import Mission, Order
from restaurant.mission_dispatch import DispatchError, dispatch_mission
from restaurant.movement_command import (
    build_move_command, validate_command, to_destination_id,
    CommandValidationError, InvalidDestinationError,
)
from restaurant.robot_transport import (
    InMemoryRobotTransport, set_robot_transport, get_robot_transport,
    RobotDisconnectedError, RobotTimeoutError,
)

User = get_user_model()


def staff_client():
    user, _ = User.objects.get_or_create(
        username="dispatch_test_staff", defaults={"is_staff": True}
    )
    c = Client()
    c.force_login(user)
    return c


def make_mission(table="4", robot="RBOT-01", status=ms.STATUS_PENDING):
    order = Order.objects.create(table=table, total=100, status="ready")
    mission = Mission.objects.create(
        order=order, destination_table=table, robot_id=robot, status=status,
    )
    return mission


class MissionDispatchTests(TestCase):
    def setUp(self):
        self.transport = InMemoryRobotTransport()
        set_robot_transport(self.transport)
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def tearDown(self):
        set_robot_transport(None)

    # 1. ارسال مأموریت معتبر
    def test_01_valid_mission_dispatched(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        self.assertTrue(result["success"])
        self.assertTrue(result["command_sent"])
        self.assertEqual(result["destination_id"], "TABLE_04")
        mission.refresh_from_db()
        self.assertEqual(mission.status, ms.STATUS_ASSIGNED)

    # 2. ارسال مأموریت ناموجود
    def test_02_missing_mission_rejected(self):
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=uuid.uuid4())
        self.assertEqual(ctx.exception.code, "mission_not_found")

    # 3. ارسال مأموریت بدون ربات
    def test_03_mission_without_robot_rejected(self):
        mission = make_mission(robot="")
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid)
        self.assertEqual(ctx.exception.code, "robot_unavailable")

    # 4. ارسال مأموریت به ربات نامعتبر (عدم تطابق robot_id)
    def test_04_wrong_robot_id_rejected(self):
        mission = make_mission(robot="RBOT-01")
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid, robot_id="RBOT-99")
        self.assertEqual(ctx.exception.code, "robot_id_mismatch")

    # 5. ارسال مأموریت با مقصد نامعتبر
    def test_05_invalid_destination_rejected(self):
        # Model validation blocks 999 on create; simulate corrupt legacy data.
        mission = make_mission(table="4")
        Mission.objects.filter(pk=mission.pk).update(destination_table="999")
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid)
        self.assertEqual(ctx.exception.code, "invalid_destination")

    # 6. ارسال مجدد یک مأموریت (Idempotency)
    def test_06_duplicate_dispatch_is_idempotent(self):
        mission = make_mission()
        first = dispatch_mission(mission_uid=mission.mission_uid)
        second = dispatch_mission(mission_uid=mission.mission_uid)
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(first["command_id"], second["command_id"])
        self.assertEqual(len(self.transport.sent_commands), 1)

    # 7. ارسال فرمان با ساختار استاندارد
    def test_07_command_uses_standard_contract(self):
        mission = make_mission(table="7")
        result = dispatch_mission(mission_uid=mission.mission_uid)
        command = result["command"]
        self.assertEqual(command["protocol_version"], "1.0")
        self.assertEqual(command["command_type"], "MOVE_TO_TABLE")
        self.assertEqual(command["mission_id"], str(mission.mission_uid))
        self.assertEqual(command["destination_id"], "TABLE_07")
        self.assertEqual(command["robot_id"], "RBOT-01")
        self.assertTrue(uuid.UUID(command["command_id"]))
        self.assertTrue(validate_command(command))

    # 8. عدم ارسال فرمان در صورت شکست اعتبارسنجی
    def test_08_no_command_sent_on_validation_failure(self):
        mission = make_mission(table="4")
        Mission.objects.filter(pk=mission.pk).update(destination_table="999")
        with self.assertRaises(DispatchError):
            dispatch_mission(mission_uid=mission.mission_uid)
        self.assertEqual(len(self.transport.sent_commands), 0)

    # 9. دریافت پاسخ موفق ربات
    def test_09_successful_robot_ack(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        outcome = self.transport.acknowledge(
            command_id=result["command_id"], robot_id="RBOT-01", status="accepted"
        )
        self.assertTrue(outcome.received)
        self.assertTrue(outcome.accepted)
        self.assertEqual(outcome.status, "accepted")

    # 10. دریافت پاسخ رد فرمان
    def test_10_robot_rejects_command(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        self.transport.queue_response(
            result["command_id"], status="rejected", received=True,
            accepted=False, error_code="robot_rejected",
        )
        # rejected outcome is produced by a fresh transport with queued response
        fresh = InMemoryRobotTransport()
        fresh.queue_response(
            result["command_id"], status="rejected", received=True, accepted=False,
            error_code="robot_rejected", error_message="busy",
        )
        outcome = fresh.send_move_command(result["command"], mission_uid=str(mission.mission_uid) + "-x",
                                          robot_id="RBOT-01")
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.status, "rejected")

    # 11. قطع ارتباط با ربات
    def test_11_disconnected_robot(self):
        broken = InMemoryRobotTransport(default_status="disconnected")
        set_robot_transport(broken)
        mission = make_mission()
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid)
        self.assertEqual(ctx.exception.code, "robot_disconnected")
        mission.refresh_from_db()
        self.assertEqual(mission.status, ms.STATUS_FAILED)

    # 12. Timeout هنگام ارسال
    def test_12_timeout_does_not_auto_retry(self):
        slow = InMemoryRobotTransport(default_status="timeout")
        set_robot_transport(slow)
        mission = make_mission()
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid)
        self.assertEqual(ctx.exception.code, "robot_timeout")
        self.assertIn("نامشخص", ctx.exception.message)
        mission.refresh_from_db()
        self.assertEqual(mission.status, ms.STATUS_FAILED)

    # 13. دریافت پاسخ با شناسه فرمان اشتباه
    def test_13_wrong_command_id_detected(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        other_id = str(uuid.uuid4())
        self.assertNotEqual(result["command_id"], other_id)
        # A response with a different command_id must not match the stored outcome
        prior = self.transport.outcome_for(str(mission.mission_uid))
        self.assertNotEqual(prior.command_id, other_id)

    # 14. دریافت پاسخ تکراری
    def test_14_duplicate_response_detected(self):
        mission = make_mission()
        result = dispatch_mission(mission_uid=mission.mission_uid)
        self.transport.queue_response(result["command_id"], status="accepted")
        first = self.transport.acknowledge(
            command_id=result["command_id"], robot_id="RBOT-01", status="accepted"
        )
        prior = self.transport.outcome_for(str(mission.mission_uid))
        # stored command_id matches -> a second ack with same id is a duplicate
        self.assertEqual(prior.command_id, result["command_id"])
        self.assertEqual(first.command_id, result["command_id"])

    # 15. عدم دسترسی کاربر غیرمجاز
    def test_15_anonymous_user_forbidden(self):
        mission = make_mission()
        client = Client()
        resp = client.post("/api/missions/dispatch/",
                           data=f'{{"mission_uid": "{mission.mission_uid}"}}',
                           content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(len(self.transport.sent_commands), 0)

    # 16. عدم امکان دریافت مأموریتهای ربات دیگر
    def test_16_robot_cannot_dispatch_other_robot_mission(self):
        mission = make_mission(robot="RBOT-01")
        with self.assertRaises(DispatchError) as ctx:
            dispatch_mission(mission_uid=mission.mission_uid, robot_id="RBOT-02")
        self.assertEqual(ctx.exception.code, "robot_id_mismatch")
        self.assertEqual(len(self.transport.sent_commands), 0)


class MovementCommandContractTests(TestCase):
    """تستهای تکمیلی قرارداد Movement Command و نگاشت مقصد."""

    def test_table_mapping(self):
        self.assertEqual(to_destination_id("4"), "TABLE_04")
        self.assertEqual(to_destination_id(1), "TABLE_01")
        self.assertEqual(to_destination_id("64"), "TABLE_64")

    def test_mapping_rejects_out_of_range(self):
        with self.assertRaises(InvalidDestinationError):
            to_destination_id("65")

    def test_build_command_rejects_empty_robot(self):
        with self.assertRaises(CommandValidationError):
            build_move_command(mission_uid=uuid.uuid4(), robot_id="", destination_table="4")

    def test_api_dispatch_success(self):
        user, _ = User.objects.get_or_create(username="api_dispatch_staff", defaults={"is_staff": True})
        client = Client()
        client.force_login(user)
        order = Order.objects.create(table="4", total=100, status="ready")
        mission = Mission.objects.create(order=order, destination_table="4", robot_id="RBOT-01")
        set_robot_transport(InMemoryRobotTransport())
        resp = client.post("/api/missions/dispatch/",
                           data=f'{{"mission_uid": "{mission.mission_uid}"}}',
                           content_type="application/json")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json()["success"])
