"""
تست‌های امنیتی سیستم پیگیری سفارش (Secure Order Tracking).

پوشش: IDOR، توکن نامعتبر/متعلق به سفارش دیگر، نبود اعتبارنامه،
Idempotency و بازیابی توکن، Rate Limiting، عدم افشای اطلاعات،
تفکیک دسترسی مشتری از آشپزخانه و سفارش‌های قدیمی.
"""

import hashlib
import json
import uuid

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase

from .models import Category, Food, Order
from .order_security import TOKEN_HEADER
from .qr_generator import generate_table_signature


class OrderTrackingSecurityTests(TestCase):

    def setUp(self):
        # Rate limit در Cache نگهداری می‌شود؛ هر تست از صفر شروع کند.
        cache.clear()

        self.category = Category.objects.create(name="غذای امنیتی", icon="🍽")
        self.food = Food.objects.create(
            category=self.category,
            name="پیتزا",
            price=100000,
            stock=500,
        )

        self.owner = Client(enforce_csrf_checks=False)
        self.attacker = Client(enforce_csrf_checks=False)
        # Kitchen list/dashboard are staff-only.
        staff, _ = User.objects.get_or_create(
            username="security_staff", defaults={"is_staff": True}
        )
        self.staff = Client(enforce_csrf_checks=False)
        self.staff.force_login(staff)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _create_order(self, client, table=1, seat="T1-top", key=None, name="مشتری"):
        payload = {
            "table": table,
            "table_signature": generate_table_signature(table),
            "seats": [seat],
            "name": name,
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": key or uuid.uuid4().hex,
        }
        return client.post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _new_order(self, client, **kwargs):
        response = self._create_order(client, **kwargs)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        return body["order_id"], body["tracking_token"]

    def _track(self, client, order_id, token=None, **extra_headers):
        headers = dict(extra_headers)
        if token is not None:
            headers[TOKEN_HEADER] = token
        return client.get(f"/api/orders/{order_id}/", headers=headers)

    # ------------------------------------------------------------------
    # ۱. مالک با توکن معتبر
    # ------------------------------------------------------------------

    def test_owner_with_valid_token_can_track_own_order(self):
        order_id, token = self._new_order(self.owner)

        response = self._track(self.attacker, order_id, token)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["id"], order_id)
        self.assertEqual(body["status"], "new")
        self.assertEqual(body["table"], "1")
        self.assertEqual(len(body["items"]), 1)

    def test_authorization_bearer_token_is_accepted(self):
        order_id, token = self._new_order(self.owner)

        response = self.attacker.get(
            f"/api/orders/{order_id}/",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200, response.content)

    def test_token_is_not_stored_in_plain_text(self):
        order_id, token = self._new_order(self.owner)
        order = Order.objects.get(id=order_id)

        self.assertNotEqual(order.tracking_token_hash, token)
        self.assertEqual(
            order.tracking_token_hash,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        self.assertTrue(order.tracking_nonce)
        self.assertNotIn(token, order.tracking_nonce)

    # ------------------------------------------------------------------
    # ۲-۸. دسترسی غیرمجاز
    # ------------------------------------------------------------------

    def test_order_id_alone_is_not_enough(self):
        order_id, _token = self._new_order(self.owner)

        response = self._track(self.attacker, order_id)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_changing_order_id_does_not_expose_other_order(self):
        order_id, _token = self._new_order(self.owner, seat="T1-top")
        # سفارش دومِ یک مشتری دیگر
        other_id, _other_token = self._new_order(
            Client(), table=1, seat="T1-right"
        )

        response = self._track(self.attacker, other_id)

        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(order_id, other_id)

    def test_invalid_token_is_rejected(self):
        order_id, _token = self._new_order(self.owner)

        response = self._track(
            self.attacker, order_id, "A" * 86
        )

        self.assertEqual(response.status_code, 404)

    def test_token_of_another_order_is_rejected(self):
        first_id, first_token = self._new_order(self.owner, seat="T2-top")
        second_id, _second_token = self._new_order(
            Client(), table=2, seat="T2-right"
        )

        response = self._track(self.attacker, second_id, first_token)

        self.assertEqual(response.status_code, 404)
        self.assertNotEqual(first_id, second_id)

    def test_missing_header_is_rejected(self):
        order_id, _token = self._new_order(self.owner)

        response = self._track(self.attacker, order_id)

        self.assertEqual(response.status_code, 404)

    def test_no_credentials_at_all_is_rejected(self):
        order_id, _token = self._new_order(self.owner)

        response = Client().get(f"/api/orders/{order_id}/")

        self.assertEqual(response.status_code, 404)

    def test_token_in_query_string_is_ignored(self):
        order_id, token = self._new_order(self.owner)

        response = self.attacker.get(
            f"/api/orders/{order_id}/?token={token}&tracking_token={token}"
        )

        self.assertEqual(response.status_code, 404)

    def test_truncated_token_is_rejected(self):
        order_id, token = self._new_order(self.owner)

        response = self._track(self.attacker, order_id, token[:-1])

        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # ۹-۱۱. Refresh، بازگشت، دو مشتری یک میز
    # ------------------------------------------------------------------

    def test_session_allows_tracking_after_page_refresh(self):
        order_id, _token = self._new_order(self.owner)

        # بدون Header ولی با همان کوکی Session مرورگر مالک
        response = self.owner.get(f"/api/orders/{order_id}/")

        self.assertEqual(response.status_code, 200, response.content)

    def test_session_cookie_is_not_shared_with_other_customers(self):
        order_id, _token = self._new_order(self.owner)

        response = self.attacker.get(f"/api/orders/{order_id}/")

        self.assertEqual(response.status_code, 404)

    def test_two_customers_on_same_table_are_isolated(self):
        second_customer = Client(enforce_csrf_checks=False)

        first_id, first_token = self._new_order(self.owner, table=3, seat="T3-top")
        second_id, second_token = self._new_order(second_customer, table=3, seat="T3-right")

        first_own = self._track(self.owner, first_id, first_token)
        second_own = self._track(second_customer, second_id, second_token)

        # هر مشتری با توکن خودش سراغ سفارش دیگری می‌رود → همان 404 عمومی
        first_cross = self._track(self.owner, second_id, first_token)
        second_cross = self._track(second_customer, first_id, second_token)

        self.assertEqual(first_own.status_code, 200)
        self.assertEqual(second_own.status_code, 200)
        self.assertEqual(first_cross.status_code, 404)
        self.assertEqual(second_cross.status_code, 404)

    # ------------------------------------------------------------------
    # ۱۲. Rate Limiting
    # ------------------------------------------------------------------

    def test_repeated_guessing_is_rate_limited(self):
        order_id, _token = self._new_order(self.owner)

        statuses = []
        for _ in range(25):
            statuses.append(self._track(self.attacker, order_id, "B" * 86).status_code)

        self.assertEqual(statuses[-1], 429, statuses)
        self.assertTrue(all(code in (404, 429) for code in statuses), statuses)

    # ------------------------------------------------------------------
    # ۱۴-۱۵. تفکیک دسترسی مشتری و آشپزخانه
    # ------------------------------------------------------------------

    def test_customer_token_cannot_change_order_status(self):
        order_id, token = self._new_order(self.owner)

        for action in ("accept", "reject", "ready", "send"):
            response = self.attacker.post(
                f"/api/orders/{order_id}/{action}/",
                headers={TOKEN_HEADER: token},
            )
            self.assertEqual(response.status_code, 403, action)
            self.assertEqual(
                response.json()["code"], "customer_token_not_allowed", action
            )

        order = Order.objects.get(id=order_id)
        self.assertEqual(order.status, "new")

    def test_customer_token_cannot_adjust_timer(self):
        order_id, token = self._new_order(self.owner)

        response = self.attacker.post(
            f"/api/orders/{order_id}/timer/",
            data=json.dumps({"delta_minutes": 5}),
            content_type="application/json",
            headers={TOKEN_HEADER: token},
        )

        self.assertEqual(response.status_code, 403)

    def test_kitchen_dashboard_still_lists_orders(self):
        order_id, _token = self._new_order(self.owner)

        response = self.staff.get("/api/orders/")

        self.assertEqual(response.status_code, 200)
        ids = [o["id"] for o in response.json()]
        self.assertIn(order_id, ids)

    def test_kitchen_list_does_not_leak_tracking_tokens(self):
        order_id, token = self._new_order(self.owner)

        response = self.staff.get("/api/orders/")
        raw = response.content.decode("utf-8")
        payload = response.json()
        order_payload = next(o for o in payload if o["id"] == order_id)

        self.assertNotIn(token, raw)
        self.assertNotIn("tracking_token", order_payload)
        self.assertNotIn("tracking_nonce", order_payload)
        self.assertNotIn("tracking_token_hash", order_payload)

    # ------------------------------------------------------------------
    # اطلاعات حساس در پاسخ مشتری
    # ------------------------------------------------------------------

    def test_tracking_payload_hides_internal_fields(self):
        order_id, token = self._new_order(self.owner, name="مشتری مخفی")

        body = self._track(self.attacker, order_id, token).json()

        for forbidden in (
            "customer_phone",
            "customer_name",
            "seats",
            "tracking_token",
            "tracking_nonce",
            "tracking_token_hash",
            "idempotency_keys",
            "cooking_started_at",
        ):
            self.assertNotIn(forbidden, body, forbidden)

        self.assertIn("status", body)
        self.assertIn("items", body)
        self.assertIn("created_at", body)

    def test_response_is_not_cacheable(self):
        order_id, token = self._new_order(self.owner)

        response = self._track(self.attacker, order_id, token)

        self.assertIn("no-store", response["Cache-Control"])

    # ------------------------------------------------------------------
    # Idempotency و بازیابی توکن
    # ------------------------------------------------------------------

    def test_idempotent_retry_returns_same_token(self):
        key = uuid.uuid4().hex
        first = self._create_order(self.owner, seat="T4-top", key=key)
        second = self._create_order(self.owner, seat="T4-top", key=key)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)

        first_body, second_body = first.json(), second.json()
        self.assertEqual(first_body["order_id"], second_body["order_id"])
        self.assertTrue(second_body["replayed"])
        self.assertEqual(first_body["tracking_token"], second_body["tracking_token"])
        self.assertEqual(Order.objects.count(), 1)

    def test_retry_does_not_invalidate_previous_token(self):
        key = uuid.uuid4().hex
        first = self._create_order(self.owner, seat="T5-top", key=key)
        order_id = first.json()["order_id"]
        original_token = first.json()["tracking_token"]

        self._create_order(self.owner, seat="T5-top", key=key)

        response = self._track(Client(), order_id, original_token)

        self.assertEqual(response.status_code, 200, response.content)

    def test_replay_repairs_order_with_missing_hash(self):
        """سفارش نیمه‌کاره (nonce موجود، hash خالی) نباید توکن بی‌اعتبار بگیرد."""
        key = uuid.uuid4().hex
        first = self._create_order(self.owner, seat="T9-top", key=key)
        order_id = first.json()["order_id"]

        Order.objects.filter(id=order_id).update(tracking_token_hash="")

        replay = self._create_order(self.owner, seat="T9-top", key=key)
        self.assertEqual(replay.status_code, 200, replay.content)

        repaired_token = replay.json()["tracking_token"]
        response = self._track(Client(), order_id, repaired_token)
        self.assertEqual(response.status_code, 200, response.content)

    def test_create_order_requires_valid_table_signature_still(self):
        payload = {
            "table": 6,
            "seats": ["T6-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }
        response = self.owner.post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "signature_required")

    # ------------------------------------------------------------------
    # سفارش‌های قدیمی
    # ------------------------------------------------------------------

    def test_legacy_order_without_credentials_is_not_trackable(self):
        order = Order.objects.create(table="7", total=1000, seats="T7-top")

        response = self.attacker.get(f"/api/orders/{order.id}/")

        self.assertEqual(response.status_code, 404)

    def test_legacy_order_is_not_trackable_with_guessed_token(self):
        order = Order.objects.create(table="8", total=1000, seats="T8-top")

        response = self._track(self.attacker, order.id, "C" * 86)

        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # تأیید دریافت توسط مشتری (عملیات مشتری، فقط روی سفارش خودش)
    # ------------------------------------------------------------------

    def test_confirm_requires_ownership(self):
        order_id, _token = self._new_order(self.owner)
        Order.objects.filter(id=order_id).update(status="awaiting_confirmation")

        response = self.attacker.post(
            f"/api/orders/{order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(Order.objects.get(id=order_id).status, "awaiting_confirmation")

    def test_owner_can_confirm_own_order(self):
        order_id, token = self._new_order(self.owner)
        Order.objects.filter(id=order_id).update(status="awaiting_confirmation")

        response = self.owner.post(
            f"/api/orders/{order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: token},
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(Order.objects.get(id=order_id).status, "done")

    # ------------------------------------------------------------------
    # صفحهٔ مستقل پیگیری
    # ------------------------------------------------------------------

    def test_standalone_tracking_page_blocks_other_customers(self):
        order_id, _token = self._new_order(self.owner)

        response = self.attacker.get(f"/order/{order_id}/")

        self.assertEqual(response.status_code, 403)
        self.assertNotIn(f"#{order_id}", response.content.decode("utf-8"))

    def test_standalone_tracking_page_allows_owner_session(self):
        order_id, _token = self._new_order(self.owner)

        response = self.owner.get(f"/order/{order_id}/")

        self.assertEqual(response.status_code, 200, response.content)
