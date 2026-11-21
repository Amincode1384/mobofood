# -*- coding: utf-8 -*-
"""
تست‌های امنیتی APIها — مأموریت: تمام APIهای مدیریتی باید دارای احراز هویت معتبر باشند.

پوشش:
- درخواست ناشناس به APIهای مدیریتی/آشپزخانه/مأموریت => 403
- کاربر عادی (م usuário فاقد مجوز) => 403
- کارمند مجاز => دسترسی
- APIهای عمومی بدون اختلال کار می‌کنند
- توکن پیگیری مشتری Provincia-orderها را محافظت می‌کند
- check_customer_phone محدودیت نرخ دارد
"""

import json
import uuid

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse

from .models import Category, Food, Order
from .qr_generator import generate_table_signature


class ApiSecurityBase(TestCase):
    def setUp(self):
        cache.clear()

        self.staff = User.objects.create_user(
            username="chef", password="chef-pass-123", is_staff=True
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

        self.customer_client = Client(enforce_csrf_checks=False)
        self.customer_client.force_login(self.customer_user)

    # ------------------------------------------------------------------
    def _payload(self, table=7):
        return {
            "table": table,
            "table_signature": generate_table_signature(table),
            "seats": [f"T{table}-top"],
            "foods": [{"id": self.food.id, "qty": 1}],
            "idempotency_key": uuid.uuid4().hex,
        }


# ---------------------------------------------------------------
# ۱. Anonymous access to management APIs
# ---------------------------------------------------------------
class AnonymousManagementApiTests(ApiSecurityBase):
    """ناشناس نباید هیچ API مدیریتی را Changing ببیند یا تغییر دهد."""

    def test_anonymous_cannot_create_food(self):
        response = Client().post(
            "/api/foods/create/",
            data={
                "name": "x",
                "price": "1000",
                "stock": "1",
                "category_id": str(self.category.id),
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_required")

    def test_anonymous_cannot_toggle_food_active(self):
        response = Client().post(
            f"/api/foods/{self.food.id}/toggle-active/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_delete_food(self):
        response = Client().delete(f"/api/foods/{self.food.id}/delete/")
        self.assertEqual(response.status_code, 403)

    def test_staff_can_delete_food(self):
        response = self.staff_client.delete(f"/api/foods/{self.food.id}/delete/")
        self.assertEqual(response.status_code, 200, response.content)

    def test_anonymous_cannot_create_category(self):
        response = Client().post(
            "/api/categories/create/",
            data=json.dumps({"name": "دسته امنیتی", "icon": "🍕"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_generate_all_qr_codes(self):
        response = Client().post(
            "/qr/generate-all/", data=json.dumps({}), content_type="application/json"
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_read_kitchen_orders(self):
        response = Client().get("/api/orders/")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "kitchen_auth_required")

    def test_anonymous_cannot_dispatch_mission(self):
        response = Client().post(
            "/api/missions/dispatch/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_anonymous_cannot_list_missions(self):
        response = Client().get("/api/missions/")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_dashboard_redirects(self):
        response = Client().get("/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])


# ---------------------------------------------------------------
# ۲. Non-staff authenticated users
# ---------------------------------------------------------------
class NonStaffManagementApiTests(ApiSecurityBase):
    """کاربر واردشده بدون مجوز کارManage نباید به APIهای مدیریتی دسترسی داشته باشد."""

    def test_customer_cannot_create_food(self):
        response = self.customer_client.post(
            "/api/foods/create/",
            data=json.dumps({"name": "x", "price": 1000, "stock": 1}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_read_kitchen_orders(self):
        response = self.customer_client.get("/api/orders/")
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_dispatch_mission(self):
        response = self.customer_client.post(
            "/api/missions/dispatch/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_customer_cannot_accept_order(self):
        response = self.customer_client.post(
            f"/api/orders/{self.order.id}/accept/",
            data=json.dumps({}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Order.objects.get(id=self.order.id).status, "new")


# ---------------------------------------------------------------
# ۳. Staff authorized access
# ---------------------------------------------------------------
class StaffAccessTests(ApiSecurityBase):
    """کارمند مجاز باید به APIهای مورد مجوزش دسترسی داشته باشد."""

    def test_staff_can_create_food(self):
        response = self.staff_client.post(
            "/api/foods/create/",
            data={
                "name": "غذا تست",
                "price": "50000",
                "stock": "5",
                "category_id": str(self.category.id),
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])

    def test_staff_can_toggle_food_active(self):
        response = self.staff_client.post(
            f"/api/foods/{self.food.id}/toggle-active/",
            data=json.dumps({"is_active": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)

    def test_staff_can_read_kitchen_orders(self):
        response = self.staff_client.get("/api/orders/")
        self.assertEqual(response.status_code, 200)

    def test_staff_can_list_missions(self):
        response = self.staff_client.get("/api/missions/")
        self.assertEqual(response.status_code, 200)

    def test_staff_dashboard_loads(self):
        response = self.staff_client.get("/dashboard/")
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------
# ۴. Public APIs still work (no regression)
# ---------------------------------------------------------------
class PublicApiStillWorksTests(ApiSecurityBase):
    """APIهای عمومی باید بدون ورود کاربر کار continued کنند."""

    def test_get_foods_is_public(self):
        response = Client().get("/api/foods/")
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json(), list)

    def test_get_seats_status_is_public(self):
        response = Client().get("/api/seats/status/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("occupied", response.json())

    def test_anonymous_can_place_order(self):
        response = Client().post(
            "/api/create_order/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertTrue(response.json()["success"])

    def test_create_order_rejects_bad_signature(self):
        payload = self._payload()
        payload["table_signature"] = "bad-signature"
        response = Client().post(
            "/api/create_order/",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_qr_code_image_is_public(self):
        response = Client().get("/qr/1/")
        self.assertIn(response.status_code, (200, 404))  # 404 if image not generated


# ---------------------------------------------------------------
# ۵. Customer token protects own orders
# ---------------------------------------------------------------
class CustomerTokenOrderTests(ApiSecurityBase):
    """فقط مالک سفارش (با توکن همان سفارش) می‌تواند پیگیری/تأیید کند."""

    def test_tracking_with_token_works(self):
        created = Client().post(
            "/api/create_order/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        ).json()
        response = Client().get(
            f"/api/orders/{created['order_id']}/",
            headers={"X-Order-Token": created["tracking_token"]},
        )
        self.assertEqual(response.status_code, 200)

    def test_tracking_without_token_is_denied(self):
        created = Client().post(
            "/api/create_order/",
            data=json.dumps(self._payload()),
            content_type="application/json",
        ).json()
        response = Client().get(f"/api/orders/{created['order_id']}/")
        self.assertEqual(response.status_code, 404)

    def test_customer_cannot_see_others_order(self):
        mine = Client().post(
            "/api/create_order/",
            data=json.dumps(self._payload(table=5)),
            content_type="application/json",
        ).json()
        other = Order.objects.create(table="8", total=1000, status="new")
        response = Client().get(
            f"/api/orders/{other.id}/",
            headers={"X-Order-Token": mine["tracking_token"]},
        )
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------
# ۶. Rate limiting on check_customer_phone
# ---------------------------------------------------------------
class CheckCustomerPhoneRateLimitTests(ApiSecurityBase):
    """تکرار بیش از حد بررسی تلفن باید محدود شود."""

    def test_rate_limit_triggers(self):
        client = Client()
        for _ in range(31):
            response = client.get(
                "/api/customer/check/?phone=09120000001"
            )
        self.assertEqual(response.status_code, 429)
