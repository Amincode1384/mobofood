"""
تست‌های حذف غذا، ایجاد دسته‌بندی و تغییر موجودی.
"""

import io
import json
import shutil
import tempfile
import uuid

from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import Category, Food, Order, OrderItem

User = get_user_model()

PAGE_URL = "/manage/menu/"
FOODS_URL = "/api/foods/"
CATEGORY_URL = "/api/categories/create/"
ORDER_URL = "/api/create_order/"

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="mgmt-test-media-")


def png_upload(name="dish.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (12, 12), (30, 120, 90)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class MgmtBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.staff = User.objects.create_user(
            username="mgr-mgmt", password="Str0ngPass!23", is_staff=True
        )
        self.customer_user = User.objects.create_user(
            username="guest-mgmt", password="Str0ngPass!23"
        )
        self.category = Category.objects.create(name="کباب‌ها", icon="🍢")
        self.food = Food.objects.create(
            category=self.category,
            name="کباب بختیاری",
            description="دو سیخ",
            price=320000,
            stock=6,
        )

    def delete_url(self, food_id=None):
        return f"/api/foods/{food_id or self.food.id}/delete/"

    def update_url(self, food_id=None):
        return f"/api/foods/{food_id or self.food.id}/update/"

    def order_payload(self, table=3, seat="T3-top", food=None, qty=1):
        food = food or self.food
        return {
            "table": table,
            "seats": [seat],
            "foods": [{"id": food.id, "qty": qty}],
            "idempotency_key": uuid.uuid4().hex,
        }


class DeleteFoodTests(MgmtBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_staff_can_delete_food(self):
        response = self.client.delete(self.delete_url())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["name"], "کباب بختیاری")
        self.assertFalse(Food.objects.filter(id=self.food.id).exists())

    def test_deleting_twice_returns_404(self):
        self.client.delete(self.delete_url())

        response = self.client.delete(self.delete_url())

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_unknown_id_returns_404(self):
        response = self.client.delete(self.delete_url(999999))

        self.assertEqual(response.status_code, 404)

    def test_anonymous_cannot_delete(self):
        self.client.logout()

        response = self.client.delete(self.delete_url())

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Food.objects.filter(id=self.food.id).exists())

    def test_customer_cannot_delete(self):
        self.client.logout()
        self.client.force_login(self.customer_user)

        response = self.client.delete(self.delete_url())

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Food.objects.filter(id=self.food.id).exists())

    def test_csrf_is_required_for_delete(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = client.delete(self.delete_url())

        self.assertEqual(response.status_code, 403)
        self.assertTrue(Food.objects.filter(id=self.food.id).exists())

    def test_get_is_not_allowed(self):
        response = self.client.get(self.delete_url())

        self.assertEqual(response.status_code, 405)

    def test_order_history_survives_deletion(self):
        order_response = self.client.post(
            ORDER_URL,
            data=json.dumps(self.order_payload()),
            content_type="application/json",
        )
        self.assertEqual(order_response.status_code, 200)
        order_id = order_response.json()["order_id"]

        self.client.delete(self.delete_url())

        self.assertFalse(Food.objects.filter(id=self.food.id).exists())

        order = Order.objects.get(id=order_id)
        item = order.items.get()
        self.assertEqual(item.name, "کباب بختیاری")
        self.assertEqual(item.price, 320000)
        self.assertEqual(item.quantity, 1)
        self.assertEqual(order.total, 320000)

    @override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
    def test_image_file_is_removed_with_the_dish(self):
        food = Food.objects.create(
            category=self.category,
            name="با تصویر",
            price=1000,
            stock=1,
            image=png_upload(),
        )
        path = food.image.path

        import os

        self.assertTrue(os.path.exists(path))

        response = self.client.delete(self.delete_url(food.id))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(path))


class CategoryTests(MgmtBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_staff_creates_category(self):
        response = self.client.post(CATEGORY_URL, {"name": "دسر", "icon": "🍰"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertFalse(body["existing"])
        self.assertEqual(body["category"]["name"], "دسر")

        self.assertTrue(Category.objects.filter(name="دسر", icon="🍰").exists())

    def test_icon_defaults_when_empty(self):
        self.client.post(CATEGORY_URL, {"name": "نوشیدنی", "icon": ""})

        category = Category.objects.get(name="نوشیدنی")
        self.assertTrue(category.icon)

    def test_empty_name_rejected(self):
        before = Category.objects.count()

        response = self.client.post(CATEGORY_URL, {"name": ""})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertEqual(Category.objects.count(), before)

    def test_whitespace_name_rejected(self):
        response = self.client.post(CATEGORY_URL, {"name": "   "})

        self.assertEqual(response.status_code, 400)

    def test_too_long_name_rejected(self):
        response = self.client.post(CATEGORY_URL, {"name": "د" * 101})

        self.assertEqual(response.status_code, 400)

    def test_duplicate_name_returns_existing_category(self):
        before = Category.objects.count()

        response = self.client.post(CATEGORY_URL, {"name": "  کباب‌ها  "})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["existing"])
        self.assertEqual(body["category"]["id"], self.category.id)
        self.assertEqual(Category.objects.count(), before)

    def test_anonymous_cannot_create_category(self):
        self.client.logout()

        response = self.client.post(CATEGORY_URL, {"name": "دسر"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Category.objects.filter(name="دسر").exists())

    def test_customer_cannot_create_category(self):
        self.client.logout()
        self.client.force_login(self.customer_user)

        response = self.client.post(CATEGORY_URL, {"name": "دسر"})

        self.assertEqual(response.status_code, 403)

    def test_new_category_appears_on_page_and_in_select(self):
        self.client.post(CATEGORY_URL, {"name": "پیش‌غذا", "icon": "🥗"})

        page = self.client.get(PAGE_URL)

        self.assertContains(page, "پیش‌غذا")
        self.assertContains(page, "🥗")

    def test_creating_category_does_not_move_existing_foods(self):
        self.client.post(CATEGORY_URL, {"name": "دسر"})

        self.food.refresh_from_db()
        self.assertEqual(self.food.category_id, self.category.id)


class StockTests(MgmtBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def set_stock(self, value, food=None):
        return self.client.put(
            self.update_url(getattr(food, "id", None)),
            data=json.dumps({"stock": value}),
            content_type="application/json",
        )

    def test_mark_sold_out(self):
        response = self.set_stock(0)

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 0)

        page = self.client.get(PAGE_URL)
        self.assertContains(page, 'data-stock="0"')

        menu = json.dumps(self.client.get(FOODS_URL).json(), ensure_ascii=False)
        self.assertIn('"stock": 0', menu)

    def test_make_available_again(self):
        self.set_stock(0)
        response = self.set_stock(4)

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 4)

        page = self.client.get(PAGE_URL)
        self.assertContains(page, 'data-stock="4"')

    def test_ordering_sold_out_food_is_rejected(self):
        self.set_stock(0)

        response = self.client.post(
            ORDER_URL,
            data=json.dumps(self.order_payload()),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertIn("موجودی", response.json()["error"])
        self.assertEqual(Order.objects.count(), 0)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 0)

    def test_ordering_available_food_still_works_and_decrements(self):
        response = self.client.post(
            ORDER_URL,
            data=json.dumps(self.order_payload(qty=2)),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 4)

    def test_stock_change_does_not_touch_other_foods(self):
        other = Food.objects.create(
            category=self.category, name="جوجه", price=200000, stock=9
        )

        self.set_stock(0)

        other.refresh_from_db()
        self.assertEqual(other.stock, 9)

    def test_negative_and_invalid_stock_rejected(self):
        for value in (-1, "abc", "3.5", "", True):
            response = self.set_stock(value)
            self.assertEqual(response.status_code, 400, value)

        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 6)

    def test_two_sequential_toggles_end_in_consistent_state(self):
        self.set_stock(0)
        self.set_stock(1)

        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 1)

        page = self.client.get(PAGE_URL)
        self.assertContains(page, 'data-stock="1"')