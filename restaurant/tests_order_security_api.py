# -*- coding: utf-8 -*-
"""
تست‌های امنیتی APIهای تغییر وضعیت سفارش.

این فایل مستقیماً همان جدول سناریوهای مأموریت امنیتی را پوشش می‌دهد:
ناشناس، مشتری، کاربر فاقد مجوز، کارکنان، CSRF، انتقال غیرمجاز،
دست‌کاری شناسه، توکن پیگیری مشتری و درخواست تکراری.
"""

import json
import uuid

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from .models import Category, Food, Order
from .order_security import TOKEN_HEADER


class KitchenSecurityBase(TestCase):
    def setUp(self):
        cache.clear()

        self.staff = User.objects.create_user(
            username="chef", password="chef-pass-123", is_staff=True
        )
        self.superuser = User.objects.create_superuser(
            username="boss", email="boss@example.com", password="boss-pass-123"
        )
        self.customer_user = User.objects.create_user(
            username="guest", password="guest-pass-123"
        )

        self.category = Category.objects.create(name="غذای امنیتی", icon="🍽")
        self.food = Food.objects.create(
            category=self.category, name="کباب", price=100000, stock=100
        )
        self.order = Order.objects.create(table="9", total=100000, status="new")

        self.staff_client = Client(enforce_csrf_checks=False)
        self.staff_client.force_login(self.staff)

    # helpers -----------------------------------------------------
    def post_status(self, action, client=None, order_id=None, **extra):
        client = client or Client()
        url = f"/api/orders/{order_id or self.order.id}/{action}/"
        return client.post(
            url,
            data=json.dumps({}),
            content_type="application/json",
            **extra,
        )

    def status(self, order_id=None):
        return Order.objects.get(id=order_id or self.order.id).status

    def to_ready(self):
        self.post_status("accept", client=self.staff_client)
        self.post_status("ready", client=self.staff_client)


class AnonymousAccessTests(KitchenSecurityBase):
    """درخواست ناشناس نباید هیچ تغییری در سفارش ایجاد کند."""

    def test_anonymous_cannot_accept(self):
        response = self.post_status("accept")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "kitchen_auth_required")
        self.assertEqual(self.status(), "new")

    def test_anonymous_cannot_reject(self):
        response = self.post_status("reject")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "new")

    def test_anonymous_cannot_mark_ready(self):
        Order.objects.filter(id=self.order.id).update(status="cooking")
        response = self.post_status("ready")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "cooking")

    def test_anonymous_cannot_send_to_robot(self):
        Order.objects.filter(id=self.order.id).update(status="ready")
        response = self.post_status("send")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "ready")

    def test_anonymous_cannot_adjust_timer(self):
        Order.objects.filter(id=self.order.id).update(status="cooking")
        response = self.post_status("timer")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_resolve_issue(self):
        Order.objects.filter(id=self.order.id).update(
            status="awaiting_confirmation", customer_confirmed=False
        )
        response = self.post_status("resolve")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "awaiting_confirmation")

    def test_anonymous_cannot_list_all_orders(self):
        response = Client().get("/api/orders/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "kitchen_auth_required")

    def test_anonymous_dashboard_redirects_to_login(self):
        response = Client().get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])


class NonStaffAccessTests(KitchenSecurityBase):
    """کاربر عادی (مشتری) و کاربر فاقد مجوز آشپزخانه."""

    def test_logged_in_customer_cannot_change_status(self):
        client = Client()
        client.force_login(self.customer_user)

        for action in ("accept", "reject", "ready", "send"):
            response = self.post_status(action, client=client)
            self.assertEqual(response.status_code, 403, action)

        self.assertEqual(self.status(), "new")

    def test_logged_in_customer_cannot_read_kitchen_list(self):
        client = Client()
        client.force_login(self.customer_user)
        self.assertEqual(client.get("/api/orders/").status_code, 403)

    def test_logged_in_customer_cannot_open_dashboard(self):
        client = Client()
        client.force_login(self.customer_user)
        response = client.get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_inactive_staff_is_rejected(self):
        inactive = User.objects.create_user(
            username="retired", password="retired-pass-123", is_staff=True
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

        client = Client()
        client.force_login(self.staff)
        # is_active=False در force_login هم باید رد شود
        client.force_login(inactive)
        self.assertEqual(self.post_status("accept", client=client).status_code, 403)
        self.assertEqual(self.status(), "new")


class StaffAccessTests(KitchenSecurityBase):
    """کارکنان مجاز باید مسیر کامل را بدون اختلال طی کنند."""

    def test_staff_can_run_full_chain(self):
        self.assertEqual(
            self.post_status("accept", client=self.staff_client).status_code, 200
        )
        self.assertEqual(self.status(), "cooking")

        self.assertEqual(
            self.post_status("ready", client=self.staff_client).status_code, 200
        )
        self.assertEqual(self.status(), "ready")

        self.assertEqual(
            self.post_status("send", client=self.staff_client).status_code, 200
        )
        self.assertEqual(self.status(), "sent")

    def test_superuser_can_manage_orders(self):
        client = Client()
        client.force_login(self.superuser)
        self.assertEqual(self.post_status("accept", client=client).status_code, 200)

    def test_staff_can_read_kitchen_list(self):
        response = self.staff_client.get("/api/orders/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(self.order.id, [row["id"] for row in response.json()])

    def test_staff_can_open_dashboard(self):
        response = self.staff_client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-column="sent"')

    def test_wrong_http_method_is_rejected(self):
        self.assertEqual(
            self.staff_client.get(
                f"/api/orders/{self.order.id}/accept/"
            ).status_code,
            405,
        )
        self.assertEqual(self.status(), "new")


class CsrfProtectionTests(KitchenSecurityBase):
    """مسیر Session-based باید CSRF را الزامی کند."""

    def test_session_without_csrf_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = self.post_status("accept", client=client)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(), "new")

    def test_session_with_valid_csrf_is_accepted(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        # صفحهٔ داشبورد کوکی csrftoken را ست می‌کند
        page = client.get("/dashboard/")
        self.assertEqual(page.status_code, 200)
        token = client.cookies["csrftoken"].value

        response = self.post_status(
            "accept", client=client, HTTP_X_CSRFTOKEN=token
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status(), "cooking")

    def test_dashboard_sets_csrf_cookie(self):
        client = Client()
        client.force_login(self.staff)
        client.get("/dashboard/")
        self.assertIn("csrftoken", client.cookies)

    def test_customer_page_sets_csrf_cookie(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("csrftoken", response.cookies)


class TransitionValidationTests(KitchenSecurityBase):
    """حتی کارکنان هم نمی‌توانند انتقال غیرمجاز انجام دهند."""

    def test_staff_cannot_skip_to_robot(self):
        response = self.post_status("send", client=self.staff_client)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "new")

    def test_staff_cannot_repeat_robot_dispatch(self):
        self.to_ready()
        first = self.post_status("send", client=self.staff_client)
        self.assertEqual(first.status_code, 200)
        eta = Order.objects.get(id=self.order.id).eta_at

        second = self.post_status("send", client=self.staff_client)
        self.assertEqual(second.status_code, 400)

        order = Order.objects.get(id=self.order.id)
        self.assertEqual(order.status, "sent")
        self.assertEqual(order.eta_at, eta)

    def test_staff_cannot_confirm_as_kitchen_action(self):
        """تأیید تحویل مخصوص مشتری است؛ کارکنان بدون توکن رد می‌شوند."""
        Order.objects.filter(id=self.order.id).update(
            status="awaiting_confirmation"
        )
        response = self.staff_client.post(
            f"/api/orders/{self.order.id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.status(), "awaiting_confirmation")


class TamperingTests(KitchenSecurityBase):
    """دست‌کاری شناسه، توکن و Payload."""

    def test_unknown_order_id_gives_404(self):
        response = self.post_status("accept", client=self.staff_client, order_id=999999)
        self.assertEqual(response.status_code, 404)

    def test_customer_tracking_token_is_not_a_kitchen_credential(self):
        payload = {
            "table": 9,
            "seats": ["T9-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }
        created = Client().post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200, created.content)
        token = created.json()["tracking_token"]
        order_id = created.json()["order_id"]

        response = self.post_status(
            "accept",
            client=self.staff_client,
            order_id=order_id,
            HTTP_X_ORDER_TOKEN=token,
        )

        # کارکنان هم نباید بتوانند شناسهٔ مشتری را جای مجوز بگذارند
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "customer_token_not_allowed")
        self.assertEqual(self.status(order_id), "new")

    def test_anonymous_with_tracking_token_cannot_change_status(self):
        payload = {
            "table": 9,
            "seats": ["T9-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }
        created = Client().post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        token = created.json()["tracking_token"]
        order_id = created.json()["order_id"]

        response = Client().post(
            f"/api/orders/{order_id}/accept/",
            data=json.dumps({}),
            content_type="application/json",
            headers={TOKEN_HEADER: token},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.status(order_id), "new")

    def test_payload_cannot_forge_other_fields(self):
        response = self.staff_client.post(
            f"/api/orders/{self.order.id}/accept/",
            data=json.dumps(
                {
                    "status": "done",
                    "total": 0,
                    "table": "1",
                    "customer_phone": "09120000000",
                }
            ),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        order = Order.objects.get(id=self.order.id)
        self.assertEqual(order.status, "cooking")
        self.assertEqual(order.table, "9")
        self.assertEqual(order.total, 100000)


class CustomerFlowsStillWorkTests(KitchenSecurityBase):
    """قابلیت‌های مشتری نباید با سخت‌گیری آشپزخانه بشکنند."""

    def _create_order(self):
        payload = {
            "table": 7,
            "seats": ["T7-top"],
            "foods": [{"id": self.food.id, "qty": 2}],
            "idempotency_key": uuid.uuid4().hex,
        }
        return Client().post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_anonymous_can_still_place_order(self):
        response = self._create_order()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])

    def test_tracking_with_token_still_works(self):
        created = self._create_order().json()
        response = Client().get(
            f"/api/orders/{created['order_id']}/",
            headers={TOKEN_HEADER: created["tracking_token"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "new")

    def test_tracking_page_renders_for_owner(self):
        created = self._create_order().json()
        client = Client()
        response = client.get(
            f"/order/{created['order_id']}/",
            headers={TOKEN_HEADER: created["tracking_token"]},
        )
        self.assertEqual(response.status_code, 200)

    def test_customer_can_confirm_delivery_with_token(self):
        created = self._create_order().json()
        order_id = created["order_id"]
        Order.objects.filter(id=order_id).update(status="awaiting_confirmation")

        client = Client(enforce_csrf_checks=False)
        response = client.post(
            f"/api/orders/{order_id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: created["tracking_token"]},
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self.status(order_id), "done")

    def test_customer_cannot_confirm_someone_elses_order(self):
        created = self._create_order().json()
        other = Order.objects.create(table="8", total=1000, status="awaiting_confirmation")

        response = Client().post(
            f"/api/orders/{other.id}/confirm/",
            data=json.dumps({"received": True}),
            content_type="application/json",
            headers={TOKEN_HEADER: created["tracking_token"]},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.status(other.id), "awaiting_confirmation")


class UrlCoverageTests(KitchenSecurityBase):
    """هیچ Endpoint تغییر وضعیتی نباید بدون محافظ باقی مانده باشد."""

    def test_every_status_endpoint_requires_staff(self):
        Order.objects.filter(id=self.order.id).update(
            status="awaiting_confirmation", customer_confirmed=False
        )
        Order.objects.filter(id=self.order.id).update(status="cooking")
        endpoints = ["accept", "reject", "ready", "send", "timer", "resolve"]

        for action in endpoints:
            response = Client().post(
                f"/api/orders/{self.order.id}/{action}/",
                data=json.dumps({"action": "retry"}),
                content_type="application/json",
            )
            self.assertEqual(
                response.status_code,
                403,
                "%s برای کاربر ناشناس باز است" % action,
            )