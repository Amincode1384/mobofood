"""
تست‌های پنل مدیریت منو و سخت‌سازی create_food.

سناریوها:
- دسترسی: ناشناس / مشتری / کارمند
- ثبت واقعی در دیتابیس و نمایش در API منوی مشتری
- اعتبارسنجی: نام، قیمت، موجودی، دسته، تصویر
- امنیت: CSRF الزامی است و قابل دور زدن نیست
"""

import io
import json
import shutil
import tempfile

from PIL import Image

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings

from .models import Category, Food

User = get_user_model()

PAGE_URL = "/manage/menu/"
CREATE_URL = "/api/foods/create/"
CATEGORY_CREATE_URL = "/api/categories/create/"
FOODS_URL = "/api/foods/"

TEST_MEDIA_ROOT = tempfile.mkdtemp(prefix="menu-test-media-")


def make_image(name="dish.png", content_type="image/png", padding=0):
    """یک PNG واقعی (و اختیاراً بزرگ‌شده) برای آپلود."""
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (210, 70, 70)).save(buffer, format="PNG")
    content = buffer.getvalue() + (b"\0" * padding)
    return SimpleUploadedFile(name, content, content_type=content_type)


@override_settings(MEDIA_ROOT=TEST_MEDIA_ROOT)
class MenuManagementTestBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TEST_MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        self.staff = User.objects.create_user(
            username="manager", password="Str0ngPass!23", is_staff=True
        )
        self.customer = User.objects.create_user(
            username="guest", password="Str0ngPass!23"
        )
        self.category = Category.objects.create(name="پیتزا", icon="🍕")

    def valid_payload(self, **overrides):
        payload = {
            "name": "پیتزا پپرونی",
            "category_id": str(self.category.id),
            "price": "185000",
            "stock": "4",
            "description": "پنیر موزارلا و پپرونی تازه",
        }
        payload.update(overrides)
        return payload


class MenuPageAccessTests(MenuManagementTestBase):
    def test_anonymous_page_redirects_to_staff_login(self):
        response = self.client.get(PAGE_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
        self.assertIn("next=/manage/menu/", response["Location"])

    def test_non_staff_user_cannot_open_page(self):
        self.client.force_login(self.customer)

        response = self.client.get(PAGE_URL)

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_staff_gets_page_with_form_and_categories(self):
        self.client.force_login(self.staff)

        response = self.client.get(PAGE_URL)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mmForm"')
        self.assertContains(response, "افزودن غذای جدید")
        self.assertContains(response, "پیتزا")
        self.assertContains(response, 'name="category_id"')
        self.assertContains(response, "css/menu_admin.css")
        self.assertContains(response, "js/menu_admin.js")
        self.assertContains(response, "csrfmiddlewaretoken")
        self.assertContains(response, 'role="alert"')

    def test_dashboard_embeds_menu_management_for_staff(self):
        self.client.force_login(self.staff)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mmOpenBtn"')
        self.assertContains(response, 'id="mmOverlay"')
        self.assertContains(response, 'id="mmForm"')
        self.assertContains(response, "منوی فعلی")

    def test_dashboard_hides_menu_management_from_guests(self):
        # The whole dashboard is staff-only now, so a guest cannot see the
        # menu-management controls (or any kitchen data) at all.
        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith("/admin/login/"))


class CreateFoodSecurityTests(MenuManagementTestBase):
    def test_anonymous_cannot_create_food(self):
        response = self.client.post(CREATE_URL, self.valid_payload())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "staff_required")
        self.assertEqual(Food.objects.count(), 0)

    def test_customer_cannot_create_food(self):
        self.client.force_login(self.customer)

        response = self.client.post(CREATE_URL, self.valid_payload())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Food.objects.count(), 0)

    def test_customer_cannot_create_category(self):
        self.client.force_login(self.customer)

        response = self.client.post(CATEGORY_CREATE_URL, {"name": "دسر"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Category.objects.count(), 1)

    def test_csrf_is_required_on_create_food(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        response = client.post(CREATE_URL, self.valid_payload())

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Food.objects.count(), 0)

    def test_csrf_token_from_page_allows_creation(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)

        page = client.get(PAGE_URL)
        token = page.cookies["csrftoken"].value

        response = client.post(
            CREATE_URL, self.valid_payload(), HTTP_X_CSRFTOKEN=token
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(Food.objects.count(), 1)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.staff)

        response = self.client.get(CREATE_URL)

        self.assertEqual(response.status_code, 405)

    def test_customer_cannot_update_or_delete_food(self):
        food = Food.objects.create(
            category=self.category, name="برگر", price=100000, stock=2
        )
        self.client.force_login(self.customer)

        update = self.client.post(
            f"/api/foods/{food.id}/update/", {"price": "1"}
        )
        delete = self.client.post(f"/api/foods/{food.id}/delete/")

        self.assertEqual(update.status_code, 403)
        self.assertEqual(delete.status_code, 403)
        food.refresh_from_db()
        self.assertEqual(food.price, 100000)


class CreateFoodBehaviourTests(MenuManagementTestBase):
    def test_staff_creates_food_and_it_reaches_customer_menu(self):
        self.client.force_login(self.staff)

        response = self.client.post(CREATE_URL, self.valid_payload())
        body = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["food"]["name"], "پیتزا پپرونی")
        self.assertEqual(body["food"]["category_id"], self.category.id)

        food = Food.objects.get(pk=body["food"]["id"])
        self.assertEqual(food.price, 185000)
        self.assertEqual(food.stock, 4)
        self.assertEqual(food.category, self.category)

        # منوی مشتری از همین API می‌خواند
        menu = self.client.get(FOODS_URL)
        # JsonResponse غیر‌ASCII را escape می‌کند، پس روی JSON پارس‌شده بررسی می‌کنیم
        self.assertIn("پیتزا پپرونی", json.dumps(menu.json(), ensure_ascii=False))

        # صفحهٔ مدیریت هم بعد از Refresh همان غذا را نشان می‌دهد
        page = self.client.get(PAGE_URL)
        self.assertContains(page, "پیتزا پپرونی")

    def test_food_without_stock_is_sold_out_for_customer(self):
        self.client.force_login(self.staff)

        self.client.post(CREATE_URL, self.valid_payload(stock="0"))

        food = Food.objects.get(name="پیتزا پپرونی")
        self.assertEqual(food.stock, 0)

        menu = self.client.get(FOODS_URL)
        payload = menu.json()
        foods = payload["foods"] if isinstance(payload, dict) else payload
        flat = []
        for item in foods:
            if isinstance(item, dict) and "foods" in item:
                flat.extend(item["foods"])
            else:
                flat.append(item)

        match = [item for item in flat if item.get("name") == "پیتزا پپرونی"]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["stock"], 0)

    def test_image_upload_is_stored(self):
        self.client.force_login(self.staff)
        payload = self.valid_payload()
        payload["image"] = make_image()

        response = self.client.post(CREATE_URL, payload)

        self.assertEqual(response.status_code, 200)
        food = Food.objects.get(name="پیتزا پپرونی")
        self.assertTrue(food.image)
        self.assertIn("/media/", response.json()["food"]["image"])

    def test_persian_digits_are_understood(self):
        # کلاینت اعداد فارسی را به لاتین تبدیل می‌کند؛ سرور هم int() پایتون
        # ارقام فارسی را می‌فهمد، پس درخواست دستی با ارقام فارسی هم درست ذخیره می‌شود.
        self.client.force_login(self.staff)

        response = self.client.post(
            CREATE_URL, self.valid_payload(price="۱۲۵۰۰۰", stock="۲")
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Food.objects.get(name="پیتزا پپرونی").price, 125000)

    def test_new_category_can_be_created_by_staff_then_used(self):
        self.client.force_login(self.staff)

        created = self.client.post(CATEGORY_CREATE_URL, {"name": "دسر", "icon": "🍰"})
        self.assertEqual(created.status_code, 200)
        category_id = created.json()["category"]["id"]

        response = self.client.post(
            CREATE_URL, self.valid_payload(name="تیرامیسو", category_id=str(category_id))
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(Food.objects.filter(name="تیرامیسو", category_id=category_id).exists())


class CreateFoodValidationTests(MenuManagementTestBase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def assert_rejected(self, payload, status=400):
        response = self.client.post(CREATE_URL, payload)

        self.assertEqual(response.status_code, status)
        self.assertFalse(response.json()["success"])
        self.assertEqual(Food.objects.count(), 0)
        return response

    def test_empty_name_rejected(self):
        self.assert_rejected(self.valid_payload(name=""))

    def test_whitespace_name_rejected(self):
        self.assert_rejected(self.valid_payload(name="     "))

    def test_too_long_name_rejected(self):
        self.assert_rejected(self.valid_payload(name="غ" * 101))

    def test_missing_category_rejected(self):
        self.assert_rejected(self.valid_payload(category_id=""))

    def test_unknown_category_rejected(self):
        response = self.assert_rejected(self.valid_payload(category_id="999999"), status=404)
        self.assertIn("دسته", response.json()["error"])

    def test_negative_price_rejected(self):
        self.assert_rejected(self.valid_payload(price="-1"))

    def test_decimal_price_rejected(self):
        self.assert_rejected(self.valid_payload(price="185000.5"))

    def test_non_numeric_price_rejected(self):
        self.assert_rejected(self.valid_payload(price="abc"))

    def test_negative_stock_rejected(self):
        self.assert_rejected(self.valid_payload(stock="-3"))

    def test_too_long_description_rejected(self):
        self.assert_rejected(self.valid_payload(description="x" * 1001))

    def test_oversized_image_rejected_without_writing_anything(self):
        payload = self.valid_payload()
        payload["image"] = make_image(padding=5 * 1024 * 1024)

        response = self.assert_rejected(payload)

        self.assertIn("۵ مگابایت", response.json()["error"])

    def test_non_image_content_type_rejected(self):
        payload = self.valid_payload()
        payload["image"] = SimpleUploadedFile(
            "menu.txt", b"not an image", content_type="text/plain"
        )

        response = self.assert_rejected(payload)

        self.assertIn("فرمت", response.json()["error"])

    def test_fake_image_with_image_header_rejected(self):
        payload = self.valid_payload()
        payload["image"] = SimpleUploadedFile(
            "fake.png", b"this is definitely not a png", content_type="image/png"
        )

        response = self.assert_rejected(payload)

        self.assertIn("تصویر معتبر", response.json()["error"])
