import json
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from restaurant.models import Order, Mission

User = get_user_model()


def _staff_client():
    user, _ = User.objects.get_or_create(username="mission_test_staff", defaults={"is_staff": True})
    c = Client()
    c.force_login(user)
    return c


class MissionCodeGenerationTests(TestCase):
    """تست تولید خودکار mission_code به فرمت MSN-XXXXXX."""

    def test_mission_code_format(self):
        order = Order.objects.create(table="5", total=100, status="ready")
        m = Mission.objects.create(order=order, destination_table="5")
        self.assertTrue(m.mission_code.startswith("MSN-"), m.mission_code)
        self.assertEqual(len(m.mission_code), 10, m.mission_code)
        num_part = m.mission_code.split("-")[1]
        self.assertEqual(len(num_part), 6, num_part)
        self.assertEqual(num_part, num_part.zfill(6))

    def test_mission_code_starts_from_one(self):
        Mission.objects.all().delete()
        order = Order.objects.create(table="1", total=100, status="ready")
        m = Mission.objects.create(order=order, destination_table="1")
        self.assertEqual(m.mission_code, "MSN-000001")

    def test_mission_code_sequential(self):
        Mission.objects.all().delete()
        # Table numbers start at 1 (0 is invalid). Codes follow the creation
        # order: first mission is MSN-000001, second MSN-000002, etc.
        for i in range(1, 4):
            order = Order.objects.create(table=str(i), total=100, status="ready")
            m = Mission.objects.create(order=order, destination_table=str(i))
            expected = f"MSN-{i:06d}"
            self.assertEqual(m.mission_code, expected, f"Expected {expected}, got {m.mission_code}")

    def test_mission_uid_is_uuid(self):
        order = Order.objects.create(table="5", total=100, status="ready")
        m = Mission.objects.create(order=order, destination_table="5")
        uuid_str = str(m.mission_uid)
        self.assertEqual(len(uuid_str), 36, uuid_str)
        self.assertEqual(uuid_str.count("-"), 4, uuid_str)
        parts = uuid_str.split("-")
        self.assertEqual(len(parts[0]), 8)
        self.assertEqual(len(parts[1]), 4)
        self.assertEqual(len(parts[2]), 4)
        self.assertEqual(len(parts[3]), 4)
        self.assertEqual(len(parts[4]), 12)


class MissionUniquenessTests(TestCase):
    """تست محدودیت‌های یکتا در سطح دیتابیس."""

    def test_mission_uid_unique_enforced(self):
        order1 = Order.objects.create(table="1", total=100, status="ready")
        m1 = Mission.objects.create(order=order1, destination_table="1")
        order2 = Order.objects.create(table="2", total=200, status="ready")
        # Uniqueness is enforced both by full_clean() and by the DB constraint.
        with self.assertRaises((IntegrityError, ValidationError)):
            Mission.objects.create(
                order=order2,
                mission_uid=m1.mission_uid,
                destination_table="2",
            )

    def test_mission_code_unique_enforced(self):
        order1 = Order.objects.create(table="1", total=100, status="ready")
        m1 = Mission.objects.create(order=order1, destination_table="1")
        order2 = Order.objects.create(table="2", total=200, status="ready")
        with self.assertRaises((IntegrityError, ValidationError)):
            Mission.objects.create(
                order=order2,
                mission_code=m1.mission_code,
                destination_table="2",
            )

    def test_mission_code_and_uid_both_populated(self):
        order = Order.objects.create(table="5", total=100, status="ready")
        m = Mission.objects.create(order=order, destination_table="5")
        self.assertIsNotNone(m.mission_uid)
        self.assertIsNotNone(m.mission_code)
        self.assertNotEqual(m.mission_code, str(m.mission_uid))


class MissionListApiTests(TestCase):
    """تست GET /api/missions/ با فیلترها."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.orders = []
        self.missions = []
        for i in range(3):
            order = Order.objects.create(table=str(5 + i), total=100, status="ready")
            self.orders.append(order)
            self.missions.append(
                Mission.objects.create(order=order, destination_table=str(5 + i))
            )

    def test_list_missions_returns_count(self):
        resp = self.client.get("/api/missions/")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["count"], 3)
        self.assertEqual(len(data["missions"]), 3)

    def test_list_missions_each_has_code(self):
        resp = self.client.get("/api/missions/")
        data = resp.json()
        for m in data["missions"]:
            self.assertIn("mission_code", m)
            self.assertIn("mission_uid", m)
            self.assertTrue(m["mission_code"].startswith("MSN-"))

    def test_filter_by_status(self):
        self.missions[0].status = Mission.STATUS_ASSIGNED
        self.missions[0].save()
        resp = self.client.get("/api/missions/?status=assigned")
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["missions"][0]["mission_code"], self.missions[0].mission_code)

    def test_filter_by_table(self):
        resp = self.client.get("/api/missions/?table=5")
        data = resp.json()
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["missions"][0]["destination_table"], "5")

    def test_filter_by_q_search_code(self):
        code = self.missions[1].mission_code
        resp = self.client.get(f"/api/missions/?q={code}")
        data = resp.json()
        self.assertGreaterEqual(data["count"], 1)
        self.assertEqual(data["missions"][0]["mission_code"], code)

    def test_filter_by_q_search_order_id(self):
        oid = self.missions[1].order_id
        resp = self.client.get(f"/api/missions/?q={oid}")
        data = resp.json()
        self.assertGreaterEqual(data["count"], 1)
        # q searches across mission_code, mission_uid, order_id, destination_table
        # so the result may match any of those fields; verify at least one match has this order_id
        order_ids = [m["order_id"] for m in data["missions"]]
        self.assertIn(oid, order_ids)

    def test_anonymous_cannot_access(self):
        anon = Client()
        resp = anon.get("/api/missions/")
        self.assertEqual(resp.status_code, 403)


class MissionDetailApiTests(TestCase):
    """تست GET /api/missions/<uuid>/."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.order = Order.objects.create(table="7", total=200, status="ready")
        self.mission = Mission.objects.create(order=self.order, destination_table="7")

    def test_detail_returns_mission(self):
        resp = self.client.get(f"/api/missions/{self.mission.mission_uid}/")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["mission"]["mission_code"], self.mission.mission_code)
        self.assertEqual(data["mission"]["status"], Mission.STATUS_PENDING)
        self.assertEqual(data["mission"]["destination_table"], "7")
        self.assertEqual(data["mission"]["order_id"], self.order.id)

    def test_detail_includes_order_info(self):
        resp = self.client.get(f"/api/missions/{self.mission.mission_uid}/")
        data = resp.json()
        self.assertIn("order", data["mission"])
        self.assertEqual(data["mission"]["order"]["id"], self.order.id)
        self.assertEqual(data["mission"]["order"]["table"], "7")

    def test_detail_not_found(self):
        fake_uid = "00000000-0000-0000-0000-000000000001"
        resp = self.client.get(f"/api/missions/{fake_uid}/")
        self.assertEqual(resp.status_code, 404, resp.content)
        data = resp.json()
        self.assertFalse(data["success"])

    def test_anonymous_cannot_access(self):
        anon = Client()
        resp = anon.get(f"/api/missions/{self.mission.mission_uid}/")
        self.assertEqual(resp.status_code, 403)


class MissionStatusUpdateApiTests(TestCase):
    """تست POST /api/missions/<uuid>/status/ و broadcast."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.order = Order.objects.create(table="9", total=300, status="ready")
        self.mission = Mission.objects.create(order=self.order, destination_table="9")

    def test_update_to_assigned(self):
        resp = self.client.post(
            f"/api/missions/{self.mission.mission_uid}/status/",
            {"status": Mission.STATUS_ASSIGNED, "robot_id": "RB-101"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["status"], Mission.STATUS_ASSIGNED)
        self.assertEqual(data["mission_code"], self.mission.mission_code)

    def test_update_to_in_progress(self):
        resp = self.client.post(
            f"/api/missions/{self.mission.mission_uid}/status/",
            {"status": Mission.STATUS_IN_PROGRESS},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.status, Mission.STATUS_IN_PROGRESS)
        self.assertIsNotNone(self.mission.started_at)

    def test_update_to_completed_sets_completed_at(self):
        resp = self.client.post(
            f"/api/missions/{self.mission.mission_uid}/status/",
            {"status": Mission.STATUS_COMPLETED},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.status, Mission.STATUS_COMPLETED)
        self.assertIsNotNone(self.mission.completed_at)

    def test_update_fails_for_unknown_status(self):
        resp = self.client.post(
            f"/api/missions/{self.mission.mission_uid}/status/",
            {"status": "not_a_real_status"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400, resp.content)
        data = resp.json()
        self.assertIn("error", data)

    def test_update_fails_for_anonymous(self):
        anon = Client()
        resp = anon.post(
            f"/api/missions/{self.mission.mission_uid}/status/",
            {"status": Mission.STATUS_ASSIGNED},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 403)

    def test_update_fails_for_nonexistent_mission(self):
        fake_uid = "00000000-0000-0000-0000-000000000099"
        resp = self.client.post(
            f"/api/missions/{fake_uid}/status/",
            {"status": Mission.STATUS_ASSIGNED},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 404, resp.content)


class SendOrderUpdatesMissionTests(TestCase):
    """تست POST /api/orders/<id>/send/ بروزرسانی مأموریت."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def test_send_order_sets_mission_in_progress(self):
        order = Order.objects.create(table="3", total=150, status="ready")
        mission = Mission.objects.create(order=order, destination_table="3")
        self.assertEqual(mission.status, Mission.STATUS_PENDING)
        self.assertIsNone(mission.started_at)

        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data["success"])

        mission.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, "sent")
        self.assertEqual(mission.status, Mission.STATUS_IN_PROGRESS)
        self.assertIsNotNone(mission.started_at)
        self.assertEqual(data.get("mission_code"), mission.mission_code)

    def test_send_order_without_mission_does_not_crash(self):
        order = Order.objects.create(table="4", total=100, status="ready")
        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 200, resp.content)
        order.refresh_from_db()
        self.assertEqual(order.status, "sent")

    def test_send_order_response_includes_mission_code(self):
        order = Order.objects.create(table="5", total=200, status="ready")
        mission = Mission.objects.create(order=order, destination_table="5")
        resp = self.client.post(f"/api/orders/{order.id}/send/")
        data = resp.json()
        self.assertEqual(data.get("mission_code"), mission.mission_code)


class ReadyOrderBroadcastsMissionUpdatedTests(TestCase):
    """تست پخش رویداد mission_updated هنگام ایجاد مأموریت در ready_order."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.fanout_calls = []

    def test_ready_order_broadcasts_mission_updated(self):
        from restaurant.events import broadcast_order_event

        original = broadcast_order_event

        def capture(event_type, payload):
            if event_type == "mission_updated":
                self.fanout_calls.append(payload)
            return original(event_type, payload)

        import restaurant.views as views_module
        views_module.broadcast_order_event = capture

        try:
            order = Order.objects.create(table="6", total=100, status="cooking")
            resp = self.client.post(f"/api/orders/{order.id}/ready/")
            self.assertEqual(resp.status_code, 200, resp.content)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertTrue(data["mission_created"])

            mission = Mission.objects.get(order=order)
            self.assertEqual(len(self.fanout_calls), 1)
            call = self.fanout_calls[0]
            self.assertEqual(call["mission_code"], mission.mission_code)
            self.assertEqual(call["mission_uid"], str(mission.mission_uid))
            self.assertEqual(call["status"], Mission.STATUS_PENDING)
            self.assertEqual(call["order_id"], order.id)
        finally:
            views_module.broadcast_order_event = original

    def test_repeat_ready_order_does_not_broadcast_mission_updated(self):
        from restaurant.events import broadcast_order_event

        original = broadcast_order_event

        def capture(event_type, payload):
            if event_type == "mission_updated":
                self.fanout_calls.append(payload)
            return original(event_type, payload)

        import restaurant.views as views_module
        views_module.broadcast_order_event = capture

        try:
            order = Order.objects.create(table="7", total=100, status="cooking")
            # اولی: ایجاد مأموریت
            self.client.post(f"/api/orders/{order.id}/ready/")
            mission = Mission.objects.get(order=order)
            self.fanout_calls.clear()
            # دومی: tanpa ایجاد مجدد
            resp = self.client.post(f"/api/orders/{order.id}/ready/")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data["success"])
            self.assertFalse(data["mission_created"])
            # نباید mission_updated پخش شود
            mission_updates = [c for c in self.fanout_calls if c.get("event") == "mission_updated" or "mission_code" in c]
            self.assertEqual(len(mission_updates), 0)
        finally:
            views_module.broadcast_order_event = original


class SendOrderDispatchTests(TestCase):
    """send_order سفارش ready را ارسال و مأموریت را به in_progress پیشبرد میکند."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()

    def _create_mission(self, status):
        order = Order.objects.create(table="10", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="10",
            status=status,
        )
        return order, mission

    def test_send_from_assigned_sets_mission_in_progress(self):
        order, mission = self._create_mission(Mission.STATUS_ASSIGNED)

        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data.get("success"))
        mission.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual(order.status, "sent")
        self.assertEqual(mission.status, Mission.STATUS_IN_PROGRESS)
        self.assertIsNotNone(mission.started_at)
        self.assertEqual(data.get("mission_code"), mission.mission_code)

    def test_send_from_pending_is_dispatched(self):
        # A freshly created mission is pending; dispatch starts it directly.
        order, mission = self._create_mission(Mission.STATUS_PENDING)

        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("success"))
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_IN_PROGRESS)

    def test_send_without_mission_creates_one(self):
        # Old/partial data must not crash dispatch; a mission is created.
        order = Order.objects.create(table="11", total=100, status="ready")

        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("success"))
        self.assertTrue(Mission.objects.filter(order=order).exists())

    def test_send_from_in_progress_rejected(self):
        order, mission = self._create_mission(Mission.STATUS_IN_PROGRESS)

        resp = self.client.post(f"/api/orders/{order.id}/send/")
        self.assertEqual(resp.status_code, 400, resp.content)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_IN_PROGRESS)


class CancelMissionApiTests(TestCase):
    """تست POST /api/missions/<uuid>/cancel/ و پخش رویداد لغو."""

    def setUp(self):
        self.client = _staff_client()
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.fanout_calls = []

    def test_cancel_from_assigned(self):
        order = Order.objects.create(table="12", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="12",
            status=Mission.STATUS_ASSIGNED,
            robot_id="RB-101",
        )

        resp = self.client.post(
            f"/api/missions/{mission.mission_uid}/cancel/",
            {"reason": "مشتری đổi میز"},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("status"), Mission.STATUS_CANCELLED)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_CANCELLED)
        self.assertEqual(mission.error_message, "مشتری đổi میز")
        self.assertIsNotNone(mission.completed_at)

    def test_cancel_from_in_progress(self):
        order = Order.objects.create(table="13", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="13",
            status=Mission.STATUS_IN_PROGRESS,
        )

        resp = self.client.post(f"/api/missions/{mission.mission_uid}/cancel/")
        self.assertEqual(resp.status_code, 200)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_CANCELLED)

    def test_cancel_from_pending_allowed(self):
        # A pending (not yet claimed) mission can be cancelled by staff.
        order = Order.objects.create(table="14", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="14",
            status=Mission.STATUS_PENDING,
        )

        resp = self.client.post(f"/api/missions/{mission.mission_uid}/cancel/")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertTrue(resp.json().get("success"))
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_CANCELLED)

    def test_cancel_from_final_rejected(self):
        order = Order.objects.create(table="15", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="15",
            status=Mission.STATUS_COMPLETED,
        )

        resp = self.client.post(f"/api/missions/{mission.mission_uid}/cancel/")
        self.assertEqual(resp.status_code, 409)
        mission.refresh_from_db()
        self.assertEqual(mission.status, Mission.STATUS_COMPLETED)

    def test_cancel_not_found(self):
        fake_uid = "00000000-0000-0000-0000-000000000099"
        resp = self.client.post(f"/api/missions/{fake_uid}/cancel/")
        self.assertEqual(resp.status_code, 404)

    def test_cancel_anonymous_rejected(self):
        order = Order.objects.create(table="16", total=100, status="ready")
        mission = Mission.objects.create(
            order=order,
            destination_table="16",
            status=Mission.STATUS_ASSIGNED,
        )

        anon = Client()
        resp = anon.post(f"/api/missions/{mission.mission_uid}/cancel/")
        self.assertEqual(resp.status_code, 403)


class MissionCodeImmutabilityTests(TestCase):
    """تست ثابت‌ماندن mission_code پس از ایجاد."""

    def setUp(self):
        Mission.objects.all().delete()
        Order.objects.all().delete()
        self.order = Order.objects.create(table="8", total=100, status="ready")
        self.mission = Mission.objects.create(order=self.order, destination_table="8")
        self.original_code = self.mission.mission_code
        self.original_uid = str(self.mission.mission_uid)

    def test_mission_code_unchanged_after_status_change(self):
        self.mission.status = Mission.STATUS_IN_PROGRESS
        self.mission.save()
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.mission_code, self.original_code)

    def test_mission_code_unchanged_after_robot_assignment(self):
        self.mission.robot_id = "RB-999"
        self.mission.status = Mission.STATUS_ASSIGNED
        self.mission.save()
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.mission_code, self.original_code)

    def test_mission_code_unchanged_after_completed(self):
        self.mission.status = Mission.STATUS_COMPLETED
        self.mission.save()
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.mission_code, self.original_code)

    def test_mission_uid_unchanged_after_any_change(self):
        self.mission.status = Mission.STATUS_FAILED
        self.mission.error_message = "Something went wrong"
        self.mission.save()
        self.mission.refresh_from_db()
        self.assertEqual(str(self.mission.mission_uid), self.original_uid)

    def test_str_representation_includes_code(self):
        s = str(self.mission)
        self.assertIn(self.mission.mission_code, s)
        self.assertIn(str(self.mission.mission_uid), s)
