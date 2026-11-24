"""
تست یکپارچه ثبت سفارش با اعتبارسنجی اطلاعات مشتری
"""

from django.test import TestCase, Client
from django.urls import reverse
from .models import Category, Food, Order
import json
import uuid


class OrderCreationIntegrationTestCase(TestCase):
    """
    تست یکپارچه فرآیند ثبت سفارش با اعتبارسنجی
    """

    def setUp(self):
        """آماده‌سازی داده‌های تست"""
        self.client = Client()

        # هر درخواست تست یک کلید Idempotency یکتا می‌گیرد تا این تست‌ها
        # رفتار عادی (بدون تکرار) را بسنجند.
        original_post = self.client.post

        def post_with_idempotency_key(url, data=None, **kwargs):
            if isinstance(data, str):
                try:
                    payload = json.loads(data)
                except (ValueError, TypeError):
                    payload = None
                if isinstance(payload, dict) and "foods" in payload \
                        and "idempotency_key" not in payload:
                    payload["idempotency_key"] = uuid.uuid4().hex
                    data = json.dumps(payload)
            return original_post(url, data=data, **kwargs)

        self.client.post = post_with_idempotency_key

        # ایجاد دسته‌بندی
        self.category = Category.objects.create(
            name="غذای اصلی",
            icon="🍕"
        )

        # ایجاد غذا
        self.food1 = Food.objects.create(
            category=self.category,
            name="پیتزا پپرونی",
            description="پیتزا خوشمزه",
            price=150000,
            stock=10
        )

        self.food2 = Food.objects.create(
            category=self.category,
            name="برگر مخصوص",
            description="برگر گوشت",
            price=120000,
            stock=15
        )

    def test_order_with_valid_phone(self):
        """تست ثبت سفارش با شماره تماس معتبر"""
        order_data = {
            "table": 3,
            "seats": ["T3-1", "T3-2"],
            "name": "علی احمدی",
            "phone": "0912 345 6789",
            "foods": [
                {"id": self.food1.id, "qty": 2},
                {"id": self.food2.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertIn('order_id', data)

        # بررسی اطلاعات ذخیره‌شده
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(order.customer_name, "علی احمدی")
        self.assertEqual(order.customer_phone, "09123456789")  # نرمال‌شده
        self.assertEqual(order.table, "3")

    def test_order_with_international_format_phone(self):
        """تست ثبت سفارش با شماره بین‌المللی"""
        order_data = {
            "table": 5,
            "seats": ["T5-1"],
            "name": "محمد رضایی",
            "phone": "+989123456789",
            "foods": [
                {"id": self.food1.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # بررسی نرمال‌سازی
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(order.customer_phone, "09123456789")

    def test_order_without_phone(self):
        """تست ثبت سفارش بدون شماره تماس (اختیاری)"""
        order_data = {
            "table": 2,
            "seats": ["T2-1"],
            "name": "مهدی حسینی",
            "phone": "",
            "foods": [
                {"id": self.food2.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])

        # بررسی ذخیره None
        order = Order.objects.get(id=data['order_id'])
        self.assertEqual(order.customer_phone, "")

    def test_order_with_invalid_phone_rejected(self):
        """تست رد سفارش با شماره نامعتبر"""
        order_data = {
            "table": 1,
            "seats": ["T1-1"],
            "name": "رضا کریمی",
            "phone": "invalid_phone",
            "foods": [
                {"id": self.food1.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])
        self.assertIn('error', data)

        # بررسی عدم ثبت سفارش
        self.assertEqual(Order.objects.count(), 0)

    def test_order_with_short_phone_rejected(self):
        """تست رد سفارش با شماره کوتاه"""
        order_data = {
            "table": 4,
            "seats": ["T4-1"],
            "name": "سارا محمدی",
            "phone": "091234567",  # کوتاه‌تر از 11 رقم
            "foods": [
                {"id": self.food1.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

        # بررسی عدم ثبت سفارش
        self.assertEqual(Order.objects.count(), 0)

    def test_order_with_letters_in_phone_rejected(self):
        """تست رد سفارش با حروف در شماره"""
        order_data = {
            "table": 6,
            "seats": ["T6-1"],
            "name": "فاطمه زارعی",
            "phone": "0912abc6789",
            "foods": [
                {"id": self.food2.id, "qty": 1}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data['success'])

        # بررسی عدم ثبت سفارش
        self.assertEqual(Order.objects.count(), 0)

    def test_stock_not_reduced_on_invalid_phone(self):
        """تست عدم کاهش موجودی در صورت شماره نامعتبر"""
        initial_stock = self.food1.stock

        order_data = {
            "table": 7,
            "seats": ["T7-1"],
            "name": "امیر حسنی",
            "phone": "invalid",
            "foods": [
                {"id": self.food1.id, "qty": 3}
            ]
        }

        response = self.client.post(
            reverse('create_order'),
            data=json.dumps(order_data),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 400)

        # بررسی عدم تغییر موجودی
        self.food1.refresh_from_db()
        self.assertEqual(self.food1.stock, initial_stock)

    def test_order_with_multiple_valid_formats(self):
        """تست قالب‌های مختلف معتبر"""
        valid_phones = [
            "09123456789",
            "+989123456789",
            "00989123456789",
            "989123456789",
            "0912 345 6789",
            "0912-345-6789"
        ]

        for idx, phone in enumerate(valid_phones):
            order_data = {
                "table": idx + 1,
                "seats": [f"T{idx}-1"],
                "name": f"مشتری {idx}",
                "phone": phone,
                "foods": [
                    {"id": self.food1.id, "qty": 1}
                ]
            }

            response = self.client.post(
                reverse('create_order'),
                data=json.dumps(order_data),
                content_type='application/json'
            )

            self.assertEqual(response.status_code, 200, f"Failed for phone: {phone}")
            data = response.json()
            self.assertTrue(data['success'])

            # همه باید به یک قالب نرمال شوند
            order = Order.objects.get(id=data['order_id'])
            self.assertEqual(order.customer_phone, "09123456789")