"""
تست‌های چرخهٔ وضعیت سفارش، تفکیک «آماده‌سازی» از «تحویل» و صحت progress.

پوشش: تمام وضعیت‌های واقعی Backend، مراحل پیگیری، برچسب مشتری، وضعیت
لغوشده/ناشناخته، انتقال‌های مجاز/غیرمجاز و رگرسیون پنل آششزخانه.
"""

import datetime
import json
import uuid

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.utils import timezone

from . import order_statuses as order_status
from .models import Category, Food, Order
from .order_security import TOKEN_HEADER
from .order_statuses import STATUS_KEYS, STATUS_LABELS
from .qr_generator import generate_table_signature


class StatusDefinitionTests(TestCase):
    """هماهنگی منبع واحد وضعیت‌ها با مدل."""

    def test_status_keys_match_model_choices(self):
        model_keys = {key for key, _label in Order.STATUS_CHOICES}
        self.assertEqual(set(STATUS_KEYS), model_keys)

    def test_every_status_has_customer_label(self):
        for key, _label in Order.STATUS_CHOICES:
            self.assertIn(key, STATUS_LABELS)
            self.assertTrue(STATUS_LABELS[key].strip())

    def test_transition_targets_are_known_statuses(self):
        model_keys = {key for key, _label in Order.STATUS_CHOICES}
        for source, targets in order_status.ALLOWED_TRANSITIONS.items():
            self.assertIn(source, model_keys)
            self.assertTrue(targets.issubset(model_keys), (source, targets))

    def test_ready_and_sent_are_distinct_from_done(self):
        self.assertNotEqual(order_status.STATUS_READY, order_status.STATUS_DONE)
        self.assertNotEqual(order_status.STATUS_SENT, order_status.STATUS_DONE)

        # غذا آماده است، ولی تحویل/تکمیل نشده
        self.assertFalse(
            order_status.can_transition(order_status.STATUS_READY, order_status.STATUS_DONE)
        )
        self.assertFalse(
            order_status.can_transition(order_status.STATUS_SENT, order_status.STATUS_DONE)
        )

    def test_cooking_cannot_be_marked_delivered(self):
        self.assertFalse(
            order_status.can_transition(order_status.STATUS_COOKING, order_status.STATUS_DONE)
        )

    def test_ready_goes_to_robot_dispatch(self):
        self.assertTrue(
            order_status.can_transition(order_status.STATUS_READY, order_status.STATUS_SENT)
        )

    def test_terminal_statuses_have_no_outgoing_transition(self):
        for terminal in (order_status.STATUS_DONE, order_status.STATUS_REJECTED):
            self.assertEqual(order_status.ALLOWED_TRANSITIONS[terminal], set())

    def test_unknown_status_has_no_transition(self):
        self.assertFalse(order_status.can_transition("mystery", order_status.STATUS_DONE))


class TrackingStatusDisplayTests(TestCase):
    """آنچه مشتری برای هر وضعیت واقعی می‌بیند."""

    def setUp(self):
        cache.clear()

        self.category = Category.objects.create(name="غذای وضعیت", icon="🍽")
        self.food = Food.objects.create(
            category=self.category, name="کباب", price=120000, stock=100
        )
        self.client = Client(enforce_csrf_checks=False)
        self.order_id, self.token = self._create_order()

    def _create_order(self):
        payload = {
            "table": 2,
            "table_signature": generate_table_signature(2),
            "seats": ["T2-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }
        response = self.client.post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()["order_id"], response.json()["tracking_token"]

    def _set_status(self, status, **fields):
        Order.objects.filter(id=self.order_id).update(status=status, **fields)

    def _track(self, token=None):
        response = Client().get(
            f"/api/orders/{self.order_id}/",
            headers={TOKEN_HEADER: token or self.token},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _current_step(self, payload):
        current = [s for s in payload["steps"] if s["state"] == "current"]
        if not current:
            return None
        self.assertEqual(len(current), 1, payload["steps"])
        return current[0]["key"]

    # ---------------------------------------------------------------
    # ۱-۸: هر وضعیت واقعی
    # ---------------------------------------------------------------

    def test_new_order_shows_registration_step(self):
        payload = self._track()

        self.assertEqual(payload["status"], "new")
        self.assertEqual(payload["status_label"], "سفارش شما ثبت شده است")
        self.assertEqual(self._current_step(payload), "new")
        self.assertEqual(payload["progress"], 10)
        self.assertFalse(payload["cancelled"])

    def test_accepted_order_maps_to_preparation_step(self):
        self._set_status("accepted")
        payload = self._track()

        self.assertEqual(payload["status_label"], "سفارش شما تأیید شده است")
        self.assertEqual(self._current_step(payload), "cooking")
        self.assertEqual(payload["progress"], 25)

    def test_cooking_order_shows_preparation(self):
        started = timezone.now() - datetime.timedelta(minutes=1)
        self._set_status(
            "cooking",
            cooking_started_at=started,
            eta_at=started + datetime.timedelta(minutes=5),
        )
        payload = self._track()

        self.assertEqual(payload["status_label"], "غذای شما در حال آماده‌سازی است")
        self.assertEqual(self._current_step(payload), "cooking")
        self.assertGreater(payload["progress"], 10)
        self.assertLessEqual(payload["progress"], 40)

    def test_ready_order_shows_food_ready_but_not_delivered(self):
        self._set_status("ready")
        payload = self._track()

        self.assertEqual(payload["status_label"], "غذای شما آماده تحویل است")
        self.assertEqual(self._current_step(payload), "ready")
        self.assertEqual(payload["progress"], 60)
        self.assertLess(payload["progress"], 100)

        done_step = next(s for s in payload["steps"] if s["key"] == "done")
        self.assertEqual(done_step["state"], "pending")
        self.assertNotIn("تحویل داده شده", payload["status_label"])

    def test_sent_order_shows_delivery_in_progress(self):
        self._set_status("sent")
        payload = self._track()

        self.assertEqual(payload["status_label"], "سفارش شما در حال ارسال است")
        self.assertEqual(self._current_step(payload), "sent")
        self.assertEqual(payload["progress"], 75)

    def test_awaiting_confirmation_is_a_real_step(self):
        self._set_status("awaiting_confirmation")
        payload = self._track()

        self.assertEqual(payload["status_label"], "در انتظار تأیید تحویل")
        self.assertEqual(self._current_step(payload), "awaiting_confirmation")
        self.assertEqual(payload["progress"], 90)

        keys = [s["key"] for s in payload["steps"]]
        self.assertIn("ready", keys)
        self.assertIn("awaiting_confirmation", keys)

    def test_done_order_completes_delivery_step(self):
        self._set_status("done")
        payload = self._track()

        self.assertEqual(payload["status_label"], "غذا به شما تحویل داده شده است")
        self.assertEqual(payload["progress"], 100)
        self.assertTrue(all(s["state"] == "done" for s in payload["steps"]))

    def test_rejected_order_is_cancelled_not_progress(self):
        self._set_status("rejected")
        payload = self._track()

        self.assertTrue(payload["cancelled"])
        self.assertEqual(payload["status_label"], "سفارش شما لغو شده است")
        self.assertEqual(payload["progress"], 0)
        self.assertIsNone(self._current_step(payload))
        self.assertTrue(all(s["state"] == "pending" for s in payload["steps"]))

    # ---------------------------------------------------------------
    # ۹-۱۱: وضعیت ناشناخته و ناسازگاری‌های قبلی
    # ---------------------------------------------------------------

    def test_unknown_status_does_not_break_tracking(self):
        self._set_status("teleported")
        payload = self._track()

        self.assertEqual(payload["status"], "teleported")
        self.assertTrue(payload["unknown_status"])
        self.assertEqual(payload["status_label"], order_status.UNKNOWN_STATUS_LABEL)
        self.assertEqual(payload["progress"], 0)
        self.assertIsNone(self._current_step(payload))

    def test_every_status_has_a_tracking_step(self):
        for status, _label in Order.STATUS_CHOICES:
            if status == order_status.STATUS_REJECTED:
                continue

            self._set_status(status)
            payload = self._track()

            if status == order_status.STATUS_DONE:
                # سفارش تکمیل‌شده مرحلهٔ «جاری» ندارد؛ همه مراحل انجام شده‌اند
                self.assertEqual(payload["progress"], 100)
                self.assertEqual(payload["steps"][-1]["state"], "done")
                continue

            self.assertIsNotNone(
                self._current_step(payload),
                f"وضعیت {status} هیچ مرحلهٔ فعالی در پیگیری ندارد",
            )

    def test_steps_always_cover_full_lifecycle(self):
        payload = self._track()
        keys = [s["key"] for s in payload["steps"]]

        self.assertEqual(
            keys,
            ["new", "cooking", "ready", "sent", "awaiting_confirmation", "done"],
        )

    def test_progress_is_monotonic_across_the_flow(self):
        chain = [
            "new",
            "accepted",
            "cooking",
            "ready",
            "sent",
            "awaiting_confirmation",
            "done",
        ]
        progresses = []
        for status in chain:
            self._set_status(status)
            progresses.append(self._track()["progress"])

        self.assertEqual(progresses, sorted(progresses), progresses)
        self.assertEqual(progresses[-1], 100)

    def test_cooking_progress_never_reaches_delivery(self):
        long_ago = timezone.now() - datetime.timedelta(hours=2)
        self._set_status(
            "cooking",
            cooking_started_at=long_ago,
            eta_at=long_ago + datetime.timedelta(minutes=5),
        )
        payload = self._track()

        self.assertLessEqual(payload["progress"], 40)
        self.assertLess(payload["progress"], 100)

    def test_refresh_shows_latest_real_status(self):
        self._set_status("ready")
        first = self._track()

        self._set_status("sent")
        after_refresh = self._track()

        self.assertEqual(first["status"], "ready")
        self.assertEqual(after_refresh["status"], "sent")
        self.assertEqual(after_refresh["progress"], 75)


class StatusTransitionApiTests(TestCase):
    """Backend فقط انتقال‌های مجاز چرخه را می‌پذیرد."""

    def setUp(self):
        cache.clear()
        self.category = Category.objects.create(name="غذای انتقال", icon="🍽")
        self.food = Food.objects.create(
            category=self.category, name="پیتزا", price=100000, stock=100
        )
        self.client = Client(enforce_csrf_checks=False)
        # Kitchen actions are staff-only; keep the public client for creating
        # the customer order, and use a separate staff client for actions.
        staff, _ = User.objects.get_or_create(
            username="flow_staff", defaults={"is_staff": True}
        )
        self.staff_client = Client(enforce_csrf_checks=False)
        self.staff_client.force_login(staff)
        self.order_id, self.token = self._create_order()

    def _create_order(self):
        payload = {
            "table": 4,
            "table_signature": generate_table_signature(4),
            "seats": ["T4-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }
        response = self.client.post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        return response.json()["order_id"], response.json()["tracking_token"]

    def _post(self, action):
        return self.staff_client.post(f"/api/orders/{self.order_id}/{action}/")

    def _status(self):
        return Order.objects.get(id=self.order_id).status

    def _track(self):
        response = Client().get(
            f"/api/orders/{self.order_id}/",
            headers={TOKEN_HEADER: self.token},
        )
        return response.json()

    def test_full_kitchen_and_delivery_chain(self):
        # new → cooking
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._status(), "cooking")

        # cooking → ready (غذا آماده، هنوز تحویل نشده)
        self.assertEqual(self._post("ready").status_code, 200)
        self.assertEqual(self._status(), "ready")

        tracking = self._track()
        self.assertEqual(tracking["progress"], 60)
        self.assertNotEqual(tracking["status"], "done")

        # ready → sent (ربات)
        self.assertEqual(self._post("send").status_code, 200)
        self.assertEqual(self._status(), "sent")

        tracking = self._track()
        self.assertEqual(tracking["progress"], 75)
        self.assertNotEqual(tracking["status"], "done")

        # sent → awaiting_confirmation (پایان مسیر ربات)
        Order.objects.filter(id=self.order_id).update(status="awaiting_confirmation")

        # awaiting → done (فقط با تأیید دریافت)
        confirm = self.client.post(
            f"/api/orders/{self.order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: self.token},
        )
        self.assertEqual(confirm.status_code, 200, confirm.content)
        self.assertEqual(self._status(), "done")
        self.assertEqual(self._track()["progress"], 100)

    def test_cannot_skip_preparation_and_deliver(self):
        response = self._post("ready")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._status(), "new")

    def test_cannot_dispatch_robot_before_food_is_ready(self):
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._post("send").status_code, 400)
        self.assertEqual(self._status(), "cooking")

    def test_cannot_accept_twice(self):
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._post("accept").status_code, 400)
        self.assertEqual(self._status(), "cooking")

    def test_cannot_reject_after_preparation_started(self):
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._post("reject").status_code, 400)
        self.assertEqual(self._status(), "cooking")

    def test_cannot_confirm_before_delivery_stage(self):
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._post("ready").status_code, 200)

        response = self.client.post(
            f"/api/orders/{self.order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: self.token},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self._status(), "ready")

    def test_rejected_order_cannot_move_forward(self):
        self.assertEqual(self._post("reject").status_code, 200)
        self.assertEqual(self._status(), "rejected")

        self.assertEqual(self._post("accept").status_code, 400)
        self.assertEqual(self._post("ready").status_code, 400)
        self.assertEqual(self._post("send").status_code, 400)
        self.assertEqual(self._status(), "rejected")

    def test_done_order_is_terminal(self):
        Order.objects.filter(id=self.order_id).update(
            status="awaiting_confirmation"
        )
        self.client.post(
            f"/api/orders/{self.order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: self.token},
        )
        self.assertEqual(self._status(), "done")

        self.assertEqual(self._post("accept").status_code, 400)
        self.assertEqual(self._post("ready").status_code, 400)
        self.assertEqual(self._post("send").status_code, 400)
        self.assertEqual(self._status(), "done")

    def test_dashboard_has_ready_column(self):
        html = self.staff_client.get("/dashboard/").content.decode("utf-8")

        self.assertIn('data-column="ready"', html)
        self.assertIn('id="colReady"', html)
        self.assertIn('id="countReady"', html)

    def test_ready_and_sent_orders_reach_kitchen_list(self):
        self._post("accept")
        self._post("ready")

        ready_payload = next(
            o for o in self.staff_client.get("/api/orders/").json() if o["id"] == self.order_id
        )
        self.assertEqual(ready_payload["status"], "ready")

        self._post("send")
        sent_payload = next(
            o for o in self.staff_client.get("/api/orders/").json() if o["id"] == self.order_id
        )
        self.assertEqual(sent_payload["status"], "sent")

    def test_kitchen_dashboard_flow_still_works(self):
        self._post("accept")
        self._post("ready")
        self._post("send")

        response = self.staff_client.get("/api/orders/")
        self.assertEqual(response.status_code, 200)

        payload = next(o for o in response.json() if o["id"] == self.order_id)
        self.assertEqual(payload["status"], "sent")
        self.assertEqual(payload["items"][0]["name"], "پیتزا")

    def test_send_sets_robot_travel_deadline(self):
        self.assertEqual(self._post("accept").status_code, 200)
        self.assertEqual(self._post("ready").status_code, 200)
        response = self._post("send")

        self.assertEqual(response.status_code, 200)
        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "sent")
        self.assertIsNotNone(order.eta_at)

    def test_robot_travel_expiry_moves_to_awaiting_not_done(self):
        from . import timer_watcher

        self._post("accept")
        self._post("ready")
        self._post("send")

        Order.objects.filter(id=self.order_id).update(
            eta_at=timezone.now() - datetime.timedelta(seconds=5)
        )
        timer_watcher.run_once()

        order = Order.objects.get(id=self.order_id)
        self.assertEqual(order.status, "awaiting_confirmation")
        self.assertNotEqual(order.status, "done")

        tracking = self._track()
        self.assertEqual(tracking["progress"], 90)
        self.assertNotEqual(tracking["status_label"], "غذا به شما تحویل داده شده است")

    def test_timer_does_not_advance_ready_orders(self):
        from . import timer_watcher

        self._post("accept")
        self._post("ready")
        Order.objects.filter(id=self.order_id).update(
            eta_at=timezone.now() - datetime.timedelta(seconds=5)
        )

        timer_watcher.run_once()

        self.assertEqual(Order.objects.get(id=self.order_id).status, "ready")

    def test_cooking_timer_expiry_still_reaches_awaiting_confirmation(self):
        from . import timer_watcher

        self._post("accept")
        Order.objects.filter(id=self.order_id).update(
            eta_at=timezone.now() - datetime.timedelta(seconds=5)
        )

        timer_watcher.run_once()

        self.assertEqual(
            Order.objects.get(id=self.order_id).status, "awaiting_confirmation"
        )

    def test_timer_expiry_reaches_awaiting_confirmation_not_done(self):
        self._post("accept")
        Order.objects.filter(id=self.order_id).update(
            eta_at=timezone.now() - datetime.timedelta(seconds=5)
        )

        # همان منطق حلقهٔ timer_watcher، بدون نیاز به Thread
        self.assertTrue(
            order_status.can_transition("cooking", "awaiting_confirmation")
        )
        Order.objects.filter(id=self.order_id).update(status="awaiting_confirmation")

        tracking = self._track()
        self.assertEqual(tracking["status"], "awaiting_confirmation")
        self.assertNotEqual(tracking["status"], "done")
        self.assertEqual(tracking["progress"], 90)
