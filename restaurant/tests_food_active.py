# -*- coding: utf-8 -*-
"""
تست‌های «فعال/غیرفعال کردن موقت غذا» (is_active).

این فیلد با `stock` (موجودی فیزیکی) فرق دارد: غذای غیرفعال حذف نمی‌شود،
در منوی مشتری نمی‌آید و ثبت سفارشش در سرور رد می‌شود.
"""

import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import Category, Food, Order, OrderItem
from .qr_generator import generate_table_signature

User = get_user_model()

PAGE_URL = "/manage/menu/"
DASHBOARD_URL = "/dashboard/"
FOODS_URL = "/api/foods/"
ORDER_URL = "/api/create_order/"


def toggle_url(food_id):
    return f"/api/foods/{food_id}/toggle-active/"


class ActiveBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="mgr-active", password="Str0ngPass!23", is_staff=True
        )
        self.customer_user = User.objects.create_user(
            username="guest-active", password="Str0ngPass!23"
        )
        self.category = Category.objects.create(name="کباب‌ها", icon="🍢")
        self.food = Food.objects.create(
            category=self.category,
            name="کباب کوبیده",
            description="دو سیخ",
            price=300000,
            stock=5,
        )

    def set_active(self, value, food_id=None, client=None):
        client = client or self.client
        return client.post(
            toggle_url(food_id if food_id is not None else self.food.id),
            data=json.dumps({"is_active": value}),
            content_type="application/json",
        )

    def order_payload(self, food=None, qty=1):
        food = food or self.food
        return {
            "table": 3,
            "table_signature": generate_table_signature(3),
            "seats": ["T3-top"],
            "foods": [{"id": food.id, "qty": qty}],
            "idempotency_key": uuid.uuid4().hex,
        }

    def place_order(self, **kwargs):
        return self.client.post(
            ORDER_URL,
            data=json.dumps(self.order_payload(**kwargs)),
            content_type="application/json",
        )


class FoodActiveModelTests(ActiveBase):
    def test_new_food_is_active_by_default(self):
        self.assertTrue(self.food.is_active)

    def test_existing_foods_stay_active_after_field_is_added(self):
        # رفتار Migration: مقدار پیش‌فرض برای ردیف‌های موجود True است.
        old = Food.objects.create(
            category=self.category, name="غذای قدیمی", price=100000, stock=2
        )
        old.refresh_from_db()
        self.assertTrue(old.is_active)

    def test_stock_and_is_active_are_independent(self):
        self.food.stock = 0
        self.food.is_active = False
        self.food.save()

        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 0)
        self.assertFalse(self.food.is_active)

        self.food.is_active = True
        self.food.save()
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 0)
        self.assertTrue(self.food.is_active)


class FoodActivePermissionTests(ActiveBase):
    def test_anonymous_cannot_toggle(self):
        response = self.set_active(False)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_required")
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_non_staff_customer_cannot_toggle(self):
        self.client.force_login(self.customer_user)

        response = self.set_active(False)

        self.assertEqual(response.status_code, 403)
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = self.set_active(False, client=client)

        self.assertEqual(response.status_code, 403)
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff)

        response = self.client.get(toggle_url(self.food.id))

        self.assertEqual(response.status_code, 405)

    def test_unknown_id_returns_404(self):
        self.client.force_login(self.staff)

        response = self.set_active(False, food_id=999999)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_missing_field_returns_400(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            toggle_url(self.food.id),
            data=json.dumps({}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_invalid_value_returns_400(self):
        self.client.force_login(self.staff)

        response = self.set_active("maybe")

        self.assertEqual(response.status_code, 400)
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_broken_json_returns_400(self):
        self.client.force_login(self.staff)

        response = self.client.post(
            toggle_url(self.food.id),
            data="{not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)


class FoodActiveToggleTests(ActiveBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_staff_can_deactivate(self):
        response = self.set_active(False)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["is_active"])
        self.assertFalse(body["food"]["is_active"])

        self.food.refresh_from_db()
        self.assertFalse(self.food.is_active)
        # حذف نشده و موجودی‌اش دست‌نخورده است
        self.assertTrue(Food.objects.filter(id=self.food.id).exists())
        self.assertEqual(self.food.stock, 5)

    def test_staff_can_reactivate(self):
        self.set_active(False)

        response = self.set_active(True)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["is_active"])
        self.food.refresh_from_db()
        self.assertTrue(self.food.is_active)

    def test_toggle_twice_returns_to_original_state(self):
        self.set_active(False)
        self.set_active(True)
        self.food.refresh_from_db()

        self.assertTrue(self.food.is_active)
        self.assertEqual(self.food.stock, 5)

    def test_string_boolean_is_accepted(self):
        response = self.set_active("false")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["is_active"])

    def test_state_persists_across_requests(self):
        self.set_active(False)

        page = self.client.get(PAGE_URL)

        self.assertContains(page, 'data-active="0"')
        self.assertContains(page, 'aria-checked="false"')

        # و در درخواست بعدی هم همان وضعیت برمی‌گردد
        again = self.client.get(PAGE_URL)
        self.assertContains(again, 'data-active="0"')

    def test_toggle_does_not_touch_other_foods(self):
        other = Food.objects.create(
            category=self.category, name="جوجه کباب", price=250000, stock=3
        )

        self.set_active(False)

        other.refresh_from_db()
        self.assertTrue(other.is_active)
        self.assertEqual(other.stock, 3)


class FoodActiveMenuTests(ActiveBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_management_page_shows_inactive_food(self):
        self.set_active(False)

        page = self.client.get(PAGE_URL)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "کباب کوبیده")
        self.assertContains(page, 'data-active="0"')
        self.assertContains(page, "خارج از منو")
        self.assertContains(page, "is-inactive")

    def test_dashboard_panel_shows_inactive_food_for_staff(self):
        self.set_active(False)

        page = self.client.get(DASHBOARD_URL)

        self.assertContains(page, "کباب کوبیده")
        self.assertContains(page, 'data-active="0"')

    def test_inactive_food_listed_as_not_served(self):
        self.set_active(False)

        menu = self.client.get(FOODS_URL).json()

        foods = [
            food
            for category in menu
            for food in category["foods"]
            if food["name"] == "کباب کوبیده"
        ]
        self.assertEqual(len(foods), 1)
        self.assertFalse(foods[0]["is_active"])
        # موجودی و قیمت دست‌نخورده به مشتری داده می‌شود
        self.assertEqual(foods[0]["stock"], 5)
        self.assertEqual(foods[0]["price"], 300000)

    def test_active_food_present_in_customer_menu_api(self):
        menu = self.client.get(FOODS_URL).json()

        foods = [food for category in menu for food in category["foods"]]
        self.assertEqual(len(foods), 1)
        self.assertEqual(foods[0]["name"], "کباب کوبیده")
        self.assertTrue(foods[0]["is_active"])

    def test_reactivated_food_returns_to_customer_menu(self):
        self.set_active(False)
        self.set_active(True)

        menu = json.dumps(self.client.get(FOODS_URL).json(), ensure_ascii=False)

        self.assertIn("کباب کوبیده", menu)


class FoodActiveOrderTests(ActiveBase):
    def test_active_food_can_be_ordered(self):
        response = self.place_order(qty=2)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 3)

    def test_inactive_food_order_is_rejected(self):
        self.client.force_login(self.staff)
        self.set_active(False)
        self.client.logout()

        response = self.place_order()

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["code"], "food_inactive")
        self.assertIn("ارائه نمی‌شود", body["error"])

        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 5)  # موجودی کم نشده

    def test_deactivation_between_menu_load_and_order_is_caught(self):
        # مشتری منو را دیده (غذا فعال است)
        self.assertIn("کباب کوبیده", json.dumps(self.client.get(FOODS_URL).json(), ensure_ascii=False))

        # مدیر در همان فاصله غذا را غیرفعال می‌کند
        self.client.force_login(self.staff)
        self.set_active(False)
        self.client.logout()

        response = self.place_order()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Order.objects.count(), 0)

    def test_reactivated_food_can_be_ordered_again(self):
        self.client.force_login(self.staff)
        self.set_active(False)
        self.set_active(True)
        self.client.logout()

        response = self.place_order()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Order.objects.count(), 1)

    def test_previous_orders_survive_deactivation(self):
        self.assertEqual(self.place_order(qty=1).status_code, 200)
        order = Order.objects.get()
        item = order.items.get()
        original_total = order.total

        self.client.force_login(self.staff)
        self.set_active(False)
        self.client.logout()

        order.refresh_from_db()
        item.refresh_from_db()

        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(order.total, original_total)
        self.assertEqual(item.name, "کباب کوبیده")
        self.assertEqual(item.price, 300000)
        self.assertEqual(item.quantity, 1)
