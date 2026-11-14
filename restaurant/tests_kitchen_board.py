# -*- coding: utf-8 -*-
"""
تست‌های برد آشپزخانه: تفکیک «آمادهٔ ارسال» و «ارسال‌شده».

هدف: مطمئن شویم وضعیت‌های ready و sent در بک‌اند درست ثبت می‌شوند،
API آشپزخانه هر دو را برمی‌گرداند، انتقال غیرمجاز رد می‌شود و ارسال دوباره
(که به معنی فرمان دوباره به ربات است) انجام نمی‌شود.
"""

import json

from django.test import TestCase

from .models import Category, Food, Order, OrderItem


class BoardBase(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name="کباب‌ها", icon="🍢")
        self.food = Food.objects.create(
            category=self.category, name="کباب", price=250000, stock=50
        )
        self.order = Order.objects.create(table="5", total=250000, status="new")
        OrderItem.objects.create(
            order=self.order, name="کباب", quantity=2, price=250000
        )

    def post(self, action, payload=None):
        return self.client.post(
            f"/api/orders/{self.order.id}/{action}/",
            data=json.dumps(payload or {}),
            content_type="application/json",
        )

    def status(self):
        self.order.refresh_from_db()
        return self.order.status

    def to_ready(self):
        self.post("accept")
        self.post("ready")


class BoardLayoutTests(BoardBase):
    def test_dashboard_has_five_separate_columns(self):
        page = self.client.get("/dashboard/").content.decode()

        for column in ["new", "cooking", "ready", "sent", "waiting"]:
            self.assertIn(f'data-column="{column}"', page)

        self.assertIn('id="colReady"', page)
        self.assertIn('id="colSent"', page)
        self.assertIn('id="countReady"', page)
        self.assertIn('id="countSent"', page)

    def test_ready_and_sent_have_different_titles(self):
        page = self.client.get("/dashboard/").content.decode()

        self.assertIn("آمادهٔ ارسال", page)
        self.assertIn("ارسال‌شده", page)


class BoardFlowTests(BoardBase):
    def test_full_flow_new_to_sent(self):
        response = self.post("accept")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status(), "cooking")

        response = self.post("ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status(), "ready")

        response = self.post("send")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.status(), "sent")
        self.assertEqual(response.json()["status"], "sent")
        self.assertTrue(response.json()["eta_at"])

    def test_sent_is_not_awaiting_confirmation(self):
        self.to_ready()
        self.post("send")

        self.assertEqual(self.status(), "sent")

    def test_cannot_send_before_ready(self):
        # از new
        response = self.post("send")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "new")

        # از cooking
        self.post("accept")
        response = self.post("send")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "cooking")

    def test_sending_twice_is_rejected(self):
        self.to_ready()
        first = self.post("send")
        self.assertEqual(first.status_code, 200)
        eta_first = first.json()["eta_at"]

        second = self.post("send")

        self.assertEqual(second.status_code, 400)
        self.assertEqual(self.status(), "sent")
        self.order.refresh_from_db()
        # زمان حرکت ربات بازنویسی نشده است (فرمان دوباره صادر نشد)
        self.assertEqual(self.order.eta_at.isoformat(), eta_first)

    def test_cannot_mark_ready_twice(self):
        self.to_ready()

        response = self.post("ready")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "ready")

    def test_cannot_accept_an_already_cooking_order(self):
        self.post("accept")

        response = self.post("accept")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "cooking")

    def test_sent_order_cannot_go_back_to_cooking(self):
        self.to_ready()
        self.post("send")

        response = self.post("ready")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.status(), "sent")


class BoardApiTests(BoardBase):
    def test_board_api_includes_ready_and_sent(self):
        ready_order = Order.objects.create(table="6", total=1000, status="ready")
        sent_order = Order.objects.create(table="7", total=2000, status="sent")

        data = self.client.get("/api/orders/").json()
        by_id = {row["id"]: row for row in data}

        self.assertEqual(by_id[ready_order.id]["status"], "ready")
        self.assertEqual(by_id[sent_order.id]["status"], "sent")

    def test_status_persists_after_refresh(self):
        self.to_ready()
        self.post("send")

        data = self.client.get("/api/orders/").json()
        row = next(item for item in data if item["id"] == self.order.id)

        self.assertEqual(row["status"], "sent")

    def test_api_returns_each_order_once(self):
        self.to_ready()

        data = self.client.get("/api/orders/").json()
        ids = [item["id"] for item in data]

        self.assertEqual(ids.count(self.order.id), 1)

    def test_order_items_unchanged_by_status_changes(self):
        self.to_ready()
        self.post("send")

        item = self.order.items.get()
        self.assertEqual(item.name, "کباب")
        self.assertEqual(item.quantity, 2)
        self.assertEqual(item.price, 250000)

    def test_customer_tracking_status_matches_board(self):
        self.to_ready()
        self.post("send")

        # مشتری هم همان وضعیت را می‌بیند (منبع: دیتابیس)
        self.assertEqual(self.status(), "sent")
