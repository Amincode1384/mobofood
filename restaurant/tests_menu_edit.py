"""
تست‌های ویرایش غذا (update_food) و جریان ویرایش در پنل مدیریت منو.
"""

import json

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Category, Food, Order, OrderItem

User = get_user_model()

UPDATE_URL = "/api/foods/{id}/update/"
PAGE_URL = "/manage/menu/"
FOODS_URL = "/api/foods/"


class UpdateFoodTestBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="mgr-edit", password="Str0ngPass!23", is_staff=True
        )
        self.customer = User.objects.create_user(
            username="guest-edit", password="Str0ngPass!23"
        )
        self.category = Category.objects.create(name="کباب‌ها", icon="🍢")
        self.food = Food.objects.create(
            category=self.category,
            name="کباب کوبیده",
            description="دو سیخ",
            price=250000,
            stock=7,
        )
        self.other = Food.objects.create(
            category=self.category,
            name="جوجه کباب",
            description="با زعفران",
            price=310000,
            stock=3,
        )

    def put(self, payload, food_id=None, client=None):
        url = UPDATE_URL.format(id=food_id or self.food.id)
        return (client or self.client).put(
            url,
            data=json.dumps(payload),
            content_type="application/json",
        )


class UpdateFoodBehaviourTests(UpdateFoodTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_updates_all_three_editable_fields(self):
        response = self.put({
            "name": "کباب کوبیده ویژه",
            "price": 295000,
            "description": "سه سیخ با گوجه کبابی",
        })

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده ویژه")
        self.assertEqual(self.food.price, 295000)
        self.assertEqual(self.food.description, "سه سیخ با گوجه کبابی")

    def test_updates_name_only_and_keeps_other_fields(self):
        response = self.put({"name": "کباب کوبیده ممتاز"})

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده ممتاز")
        self.assertEqual(self.food.price, 250000)
        self.assertEqual(self.food.description, "دو سیخ")
        self.assertEqual(self.food.stock, 7)

    def test_updates_price_only(self):
        response = self.put({"price": 270000})

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.price, 270000)
        self.assertEqual(self.food.name, "کباب کوبیده")
        self.assertEqual(self.food.description, "دو سیخ")

    def test_updates_description_only_and_allows_empty(self):
        self.put({"description": "با برنج ایرانی"})
        self.food.refresh_from_db()
        self.assertEqual(self.food.description, "با برنج ایرانی")

        response = self.put({"description": ""})
        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.description, "")

    def test_stock_is_untouched_when_not_sent(self):
        self.put({"name": "کباب کوبیده", "price": 250000})

        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, 7)

    def test_category_and_image_are_not_touched(self):
        response = self.put({"name": "کباب کوبیده", "price": 260000})
        body = response.json()

        self.assertEqual(body["food"]["category_id"], self.category.id)
        self.food.refresh_from_db()
        self.assertEqual(self.food.category_id, self.category.id)

    def test_only_target_food_changes(self):
        self.put({"name": "کباب کوبیده", "price": 1})

        self.other.refresh_from_db()
        self.assertEqual(self.other.name, "جوجه کباب")
        self.assertEqual(self.other.price, 310000)

    def test_response_contains_updated_food(self):
        response = self.put({"name": "کباب جدید", "price": 123000})
        body = response.json()

        self.assertEqual(body["food"]["name"], "کباب جدید")
        self.assertEqual(body["food"]["price"], 123000)
        self.assertEqual(body["food"]["id"], self.food.id)

    def test_customer_menu_api_reflects_the_edit(self):
        self.put({"name": "کباب مخصوص", "price": 333000})

        menu = self.client.get(FOODS_URL).json()
        text = json.dumps(menu, ensure_ascii=False)

        self.assertIn("کباب مخصوص", text)
        self.assertIn("333000", text)

    def test_page_renders_edit_controls_and_data(self):
        self.client.logout()
        self.client.force_login(self.staff)

        page = self.client.get(PAGE_URL)

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'class="mm-edit-btn"')
        self.assertContains(page, 'data-name="کباب کوبیده"')
        self.assertContains(page, 'data-price="250000"')
        self.assertContains(page, 'id="mmCancelEdit"')

    def test_page_shows_edited_values_after_refresh(self):
        self.put({"name": "کباب نهایی", "price": 999000, "description": "تازه"})

        page = self.client.get(PAGE_URL)

        self.assertContains(page, 'data-name="کباب نهایی"')
        self.assertContains(page, 'data-price="999000"')

    def test_order_history_is_not_rewritten(self):
        order = Order.objects.create(table="3", total=500000)
        item = OrderItem.objects.create(
            order=order, name="کباب کوبیده", quantity=2, price=250000
        )

        self.put({"name": "کباب لوکس", "price": 400000})

        item.refresh_from_db()
        self.assertEqual(item.name, "کباب کوبیده")
        self.assertEqual(item.price, 250000)

        order.refresh_from_db()
        self.assertEqual(order.total, 500000)


class UpdateFoodValidationTests(UpdateFoodTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def assert_rejected(self, payload, status=400):
        response = self.put(payload)

        self.assertEqual(response.status_code, status)
        self.assertFalse(response.json()["success"])

        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده")
        self.assertEqual(self.food.price, 250000)
        self.assertEqual(self.food.description, "دو سیخ")

        return response

    def test_empty_name_rejected(self):
        self.assert_rejected({"name": ""})

    def test_whitespace_name_rejected(self):
        self.assert_rejected({"name": "    "})

    def test_too_long_name_rejected(self):
        self.assert_rejected({"name": "ک" * 101})

    def test_non_string_name_rejected(self):
        self.assert_rejected({"name": 12345})

    def test_negative_price_rejected(self):
        self.assert_rejected({"price": -1})

    def test_non_numeric_price_rejected(self):
        self.assert_rejected({"price": "abc"})

    def test_decimal_price_rejected(self):
        self.assert_rejected({"price": "250000.5"})

    def test_boolean_price_rejected(self):
        self.assert_rejected({"price": True})

    def test_empty_price_rejected(self):
        self.assert_rejected({"price": ""})

    def test_too_long_description_rejected(self):
        self.assert_rejected({"description": "x" * 1001})

    def test_non_string_description_rejected(self):
        self.assert_rejected({"description": ["a"]})

    def test_unknown_food_returns_404(self):
        response = self.put({"name": "x"}, food_id=999999)

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    def test_invalid_json_body(self):
        response = self.client.put(
            UPDATE_URL.format(id=self.food.id),
            data="{not json",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده")

    def test_json_list_body_rejected(self):
        response = self.client.put(
            UPDATE_URL.format(id=self.food.id),
            data=json.dumps([1, 2, 3]),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_get_not_allowed(self):
        response = self.client.get(UPDATE_URL.format(id=self.food.id))

        self.assertEqual(response.status_code, 405)


class UpdateFoodSecurityTests(UpdateFoodTestBase):
    def test_anonymous_cannot_update(self):
        response = self.put({"name": "هک"})

        self.assertEqual(response.status_code, 403)
        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده")

    def test_customer_cannot_update(self):
        self.client.force_login(self.customer)

        response = self.put({"name": "هک"})

        self.assertEqual(response.status_code, 403)
        self.food.refresh_from_db()
        self.assertEqual(self.food.name, "کباب کوبیده")

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = self.put({"price": 1}, client=client)

        self.assertEqual(response.status_code, 403)
        self.food.refresh_from_db()
        self.assertEqual(self.food.price, 250000)

    def test_csrf_token_from_page_allows_update(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        page = client.get(PAGE_URL)
        token = page.cookies["csrftoken"].value

        url = UPDATE_URL.format(id=self.food.id)
        response = client.put(
            url,
            data=json.dumps({"price": 265000}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.price, 265000)

    def test_extra_fields_are_ignored(self):
        self.client.force_login(self.staff)

        response = self.put({
            "name": "کباب کوبیده",
            "category": 99999,
            "image": "x.png",
            "id": 12345,
        })

        self.assertEqual(response.status_code, 200)
        self.food.refresh_from_db()
        self.assertEqual(self.food.category_id, self.category.id)
        self.assertFalse(self.food.image)
