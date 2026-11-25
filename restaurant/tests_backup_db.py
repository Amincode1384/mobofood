# -*- coding: utf-8 -*-
"""
تست‌های فرمان دستی/اختیاری بکاپ دیتابیس (``backup_db``).

این تست‌ها فقط صحت رفتار فرمان را بررسی می‌کنند؛ هیچ زمان‌بندی یا اجرای
خودکاری در آن‌ها وجود ندارد (تصمیم کاربر: بکاپ لازم نیست).
"""

import json
import os
import tempfile
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from restaurant.models import Category, Food, Order


class BackupDbCommandTests(TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)
        self.out = Path(self.tmp_dir.name) / "backup.json"

    # 1) ایجاد بکاپ معتبر و قابل بازیابی
    def test_creates_valid_backup(self):
        category = Category.objects.create(name="نوشیدنی")
        Food.objects.create(
            category=category, name="چای", price=50000, stock=5,
        )
        Order.objects.create(
            customer_name="علی", customer_phone="09121234567",
            table="TABLE_01", status="new",
        )

        call_command("backup_db", output=str(self.out), verbosity=0)

        self.assertTrue(self.out.is_file(), "فایل بکاپ ساخته نشد")
        rows = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertIsInstance(rows, list)
        self.assertGreater(len(rows), 0)
        models = {row["model"] for row in rows}
        self.assertIn("restaurant.food", models)
        self.assertIn("restaurant.order", models)

    # 2) فایل بکاپ هرگز در Git ایندکس نمی‌شود
    def test_backup_output_is_gitignored(self):
        repo_root = Path(__file__).resolve().parents[1]
        gitignore = (repo_root / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("backups/", gitignore)

    # 3) fail-safe: نبود فایل دیتابیس → خطا و عدم ساخت فایل ناقص
    def test_fails_safe_when_database_missing(self):
        bad_settings = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": str(Path(self.tmp_dir.name) / "missing.sqlite3"),
            }
        }
        with override_settings(DATABASES=bad_settings):
            with self.assertRaises(CommandError) as ctx:
                call_command("backup_db", output=str(self.out), verbosity=0)
        self.assertIn("پیدا نشد", str(ctx.exception))
        self.assertFalse(self.out.exists(), "فایل ناقص در مسیر خروجی باقی مانده است")
        leftovers = list(Path(self.tmp_dir.name).glob("*.tmp"))
        self.assertEqual(leftovers, [], "فایل موقت پس از خطا پاک نشده است")

    # 4) عدم نشت secret و نبود داده‌های نشست/لاگ ادمین
    def test_no_secret_or_session_leak(self):
        secret = "s3cr3t-THIS-MUST-NOT-APPEAR-9876"
        token = "robot-THIS-MUST-NOT-APPEAR-1234"
        with override_settings(SECRET_KEY=secret, ROBOT_API_TOKEN=token):
            call_command("backup_db", output=str(self.out), verbosity=0)
        text = self.out.read_text(encoding="utf-8")
        self.assertNotIn(secret, text)
        self.assertNotIn(token, text)
        rows = json.loads(text)
        models = {row["model"] for row in rows}
        self.assertNotIn("sessions.session", models)
        self.assertNotIn("admin.logentry", models)

    # 5) --anonymize ماسک تلفن و پاک کردن نام/توکن‌ها
    def test_anonymize_masks_pii(self):
        Order.objects.create(
            customer_name="علی", customer_phone="09121234567",
            table="TABLE_02", status="new",
        )
        call_command(
            "backup_db", output=str(self.out), anonymize=True, verbosity=0,
        )
        text = self.out.read_text(encoding="utf-8")
        self.assertNotIn("09121234567", text)
        self.assertIn("4567", text)
        self.assertNotIn("علی", text)

    # 6) خروجی با loaddata قابل بازیابی است
    def test_backup_can_be_restored(self):
        category = Category.objects.create(name="دسر")
        Food.objects.create(category=category, name="کیک", price=80000, stock=3)
        Order.objects.create(
            customer_name="سارا", customer_phone="09351112233",
            table="TABLE_03", status="new",
        )
        call_command("backup_db", output=str(self.out), verbosity=0)

        Order.objects.all().delete()
        Food.objects.all().delete()
        Category.objects.all().delete()
        self.assertEqual(Order.objects.count(), 0)

        call_command("loaddata", str(self.out), verbosity=0)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(Order.objects.first().customer_name, "سارا")
        self.assertEqual(Food.objects.count(), 1)

    # 7) موتور غیر SQLite رد شود (بدون وابستگی جدید)
    def test_rejects_non_sqlite_engine(self):
        pg_settings = {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "somedb",
            }
        }
        with override_settings(DATABASES=pg_settings):
            with self.assertRaises(CommandError) as ctx:
                call_command("backup_db", output=str(self.out), verbosity=0)
        self.assertIn("SQLite", str(ctx.exception))
        self.assertFalse(self.out.exists())