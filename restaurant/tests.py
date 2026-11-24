from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse
from unittest import mock

import threading
import uuid

from .models import Category, Food, Order, OrderIdempotencyKey


class CreateOrderTableTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        cat = Category.objects.create(name="غذای تست", icon="🍽")
        self.food = Food.objects.create(
            category=cat, name="کباب", price=100000, stock=10
        )

    def _payload(self, table, seats=None):
        return {
            "table": table,
            "foods": [{"id": self.food.id, "qty": 1}],
            "seats": seats or ["T05-top"],
            "idempotency_key": uuid.uuid4().hex,
        }

    def test_valid_table_creates_order(self):
        res = self.client.post(
            "/api/create_order/",
            data=self._payload(5),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body.get("success"))
        self.assertIn("order_id", body)

    def test_missing_table_rejected(self):
        res = self.client.post(
            "/api/create_order/",
            data=self._payload(None),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertFalse(body.get("success"))
        self.assertEqual(body.get("code"), "table_required")

    def test_out_of_range_table_rejected(self):
        res = self.client.post(
            "/api/create_order/",
            data=self._payload(99),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json().get("success"))

    def test_invalid_table_type_rejected(self):
        res = self.client.post(
            "/api/create_order/",
            data=self._payload("abc"),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json().get("code"), "table_invalid_type")

class OrderResponseContractTests(TestCase):
    """قرارداد پاسخ ثبت سفارش: شناسهٔ واقعی و جلوگیری از سفارش تکراری."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        cat = Category.objects.create(name="غذای قرارداد", icon="🍽")
        self.food = Food.objects.create(
            category=cat, name="پیتزا", price=120000, stock=10
        )

    def _payload(self, table=6, seats=None):
        return {
            "table": table,
            "foods": [{"id": self.food.id, "qty": 1}],
            "seats": seats or ["T06-top"],
            "idempotency_key": uuid.uuid4().hex,
        }

    def test_response_returns_real_order_id(self):
        res = self.client.post(
            "/api/create_order/",
            data=self._payload(),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertTrue(body.get("success"))
        self.assertIsInstance(body.get("order_id"), int)

        # شناسه باید به همان سفارش واقعی در دیتابیس اشاره کند
        order = Order.objects.get(id=body["order_id"])
        self.assertEqual(order.table, "6")
        self.assertEqual(order.status, "new")
        self.assertEqual(order.items.count(), 1)
        self.assertEqual(order.seats, "T06-top")

    def test_second_logical_order_for_same_seat_rejected(self):
        # دو عملیات *متفاوت* (کلیدهای متفاوت) روی یک صندلی → دومی 409
        first = self.client.post(
            "/api/create_order/", data=self._payload(), content_type="application/json"
        )
        self.assertEqual(first.status_code, 200, first.content)

        second = self.client.post(
            "/api/create_order/", data=self._payload(), content_type="application/json"
        )
        self.assertEqual(second.status_code, 409)
        self.assertFalse(second.json().get("success"))
        self.assertEqual(Order.objects.filter(seats="T06-top").count(), 1)

    def test_error_response_never_reports_success(self):
        payload = self._payload(table=0)  # outside 1..64 -> real validation error
        res = self.client.post(
            "/api/create_order/", data=payload, content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)
        body = res.json()
        self.assertFalse(body.get("success"))
        self.assertNotIn("order_id", body)


class OrderIdempotencyTests(TestCase):
    """جلوگیری از ثبت سفارش تکراری با کلید Idempotency."""

    def setUp(self):
        self.client = Client(enforce_csrf_checks=False)
        cat = Category.objects.create(name="غذای idempotency", icon="🍽")
        self.food = Food.objects.create(
            category=cat, name="کوکو", price=90000, stock=50
        )

    def _payload(self, key, seats=None, qty=1, table=7):
        return {
            "table": table,
            "foods": [{"id": self.food.id, "qty": qty}],
            "seats": seats or ["T07-top"],
            "idempotency_key": key,
        }

    def _post(self, payload):
        return self.client.post(
            "/api/create_order/", data=payload, content_type="application/json"
        )

    def test_single_submit_creates_one_order(self):
        res = self._post(self._payload(uuid.uuid4().hex))
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderIdempotencyKey.objects.count(), 1)

    def test_same_key_replay_returns_same_order(self):
        key = uuid.uuid4().hex
        payload = self._payload(key)

        first = self._post(payload)
        second = self._post(payload)

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json()["order_id"], second.json()["order_id"])
        self.assertTrue(second.json().get("replayed"))
        self.assertEqual(Order.objects.count(), 1)

    def test_retry_after_success_does_not_broadcast_twice(self):
        key = uuid.uuid4().hex
        payload = self._payload(key)

        with mock.patch("restaurant.views.broadcast_order_event") as mocked:
            self._post(payload)
            self._post(payload)

        # رویداد «سفارش جدید» فقط یک‌بار برای آشپزخانه/ربات ارسال می‌شود
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(mocked.call_args[0][0], "new_order")
        self.assertEqual(Order.objects.count(), 1)

    def test_same_key_different_payload_rejected(self):
        key = uuid.uuid4().hex
        first = self._post(self._payload(key, seats=["T07-top"], qty=1))
        self.assertEqual(first.status_code, 200, first.content)

        conflict = self._post(self._payload(key, seats=["T07-right"], qty=3))
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(conflict.json().get("code"), "idempotency_key_conflict")
        self.assertEqual(Order.objects.count(), 1)

    def test_processing_state_when_key_without_order(self):
        key = uuid.uuid4().hex
        # fingerprint خالی = کلید بدون اثر انگشت تطبیق‌داده‌شده (در حال پردازش)
        OrderIdempotencyKey.objects.create(key=key, fingerprint="")

        res = self._post(self._payload(key))
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json().get("code"), "processing")
        self.assertEqual(Order.objects.count(), 0)

    def test_new_key_creates_new_order(self):
        first = self._post(self._payload(uuid.uuid4().hex, seats=["T07-top"]))
        second = self._post(self._payload(uuid.uuid4().hex, seats=["T07-right"]))

        self.assertEqual(first.status_code, 200, first.content)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertNotEqual(first.json()["order_id"], second.json()["order_id"])
        self.assertEqual(Order.objects.count(), 2)

    def test_missing_key_rejected(self):
        payload = self._payload(uuid.uuid4().hex)
        payload.pop("idempotency_key")

        res = self._post(payload)
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json().get("code"), "idempotency_key_required")
        self.assertEqual(Order.objects.count(), 0)

    def test_invalid_key_rejected(self):
        res = self._post(self._payload("short"))
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.json().get("code"), "idempotency_key_invalid")
        self.assertEqual(Order.objects.count(), 0)

    def test_failed_creation_leaves_no_order_or_key(self):
        # خطای بعد از ساخت سفارش باید کل تراکنش (سفارش + کلید + موجودی) را
        # برگرداند تا Retry بعدی بتواند همان عملیات را کامل کند.
        key = uuid.uuid4().hex
        stock_before = self.food.stock

        with mock.patch("restaurant.views.broadcast_order_event", side_effect=Exception("boom")):
            res = self._post(self._payload(key))

        self.assertEqual(res.status_code, 500)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderIdempotencyKey.objects.count(), 0)
        self.food.refresh_from_db()
        self.assertEqual(self.food.stock, stock_before)


class OrderIdempotencyConcurrencyTests(TransactionTestCase):
    """درخواست هم‌زمان با یک کلید → فقط یک سفارش."""

    reset_sequences = True

    def setUp(self):
        cat = Category.objects.create(name="غذای هم‌زمان", icon="🍽")
        self.food = Food.objects.create(
            category=cat, name="کباب", price=100000, stock=50
        )

    def test_concurrent_same_key_creates_single_order(self):
        key = uuid.uuid4().hex
        barrier = threading.Barrier(2)
        results = []

        def worker(seat):
            from django.db import connection
            client = Client(enforce_csrf_checks=False)
            payload = {
                "table": 8,
                "foods": [{"id": self.food.id, "qty": 1}],
                "seats": [seat],
                "idempotency_key": key,
            }
            try:
                barrier.wait(timeout=10)
                res = client.post(
                    "/api/create_order/",
                    data=payload,
                    content_type="application/json",
                )
                results.append((res.status_code, res.json()))
            except Exception as exc:  # noqa: BLE001 — ثبت نتیجه در تست
                results.append(("EXC", repr(exc)))
            finally:
                connection.close()

        threads = [
            threading.Thread(target=worker, args=("T08-top",)),
            threading.Thread(target=worker, args=("T08-right",)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        self.assertEqual(Order.objects.count(), 1, results)
        self.assertEqual(OrderIdempotencyKey.objects.count(), 1, results)

        success_ids = [body["order_id"] for code, body in results if code == 200]
        self.assertTrue(success_ids, results)
        self.assertEqual(len(set(success_ids)), 1)