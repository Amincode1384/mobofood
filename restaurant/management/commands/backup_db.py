# -*- coding: utf-8 -*-
"""
بکاپ دستی و اختیاری دیتابیس SQLite پروژه EnjiNex.

تصمیم کاربر: «بکاپ لازم نیست». بنابراین این فرمان فقط وقتی اجرا میشود که
کاربر صریحاً آن را صدا بزند؛ هیچ cron، routine، job یا schedule خودکاری آن
را فراخوانی نمیکند و در مسیر بحرانی استقرار قرار ندارد.

از ابزار استاندارد Django (``dumpdata``) استفاده میشود؛ بدون وابستگی جدید.
خروجی JSON است و با ``loaddata`` قابل بازیابی است.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from restaurant.error_logs import sanitize_error_text

DEFAULT_OUTPUT_DIRNAME = "backups"

# session keys و خلاصهٔ لاگ ادمین، راز/PII بالقوهاند و برای بکاپ لازم نیستند.
EXCLUDED_APPS = ("sessions", "admin")

# فیلدهایی که در حالت --anonymize پاک میشوند (تلفن جداگانه ماسک میشود).
BLANK_FIELDS = {
    "restaurant.order": ("customer_name", "tracking_nonce", "tracking_token_hash"),
    "restaurant.systemerrorlog": (
        "message", "order_uid", "mission_uid", "robot_id", "path",
    ),
}
PHONE_KEEP_DIGITS = 4


def mask_phone(value):
    """نمایش فقط ۴ رقم آخر تلفن؛ بقیه با ستاره پوشانده میشود."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return ""
    if len(digits) <= PHONE_KEEP_DIGITS:
        return "*" * len(digits)
    return "*" * (len(digits) - PHONE_KEEP_DIGITS) + digits[-PHONE_KEEP_DIGITS:]


class Command(BaseCommand):
    help = "بکاپ دستی/اختیاری SQLite به JSON (بدون schedule خودکار)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            dest="output",
            metavar="PATH",
            help="مسیر کامل فایل .json؛ پیشفرض backups/backup_<تاریخ>_<زمان>.json",
        )
        parser.add_argument(
            "--anonymize",
            action="store_true",
            help="ماسک تلفن و پاک کردن نام/توکنها (برای اشتراک؛ Restore کامل نیست).",
        )

    def handle(self, *args, **options):
        db_conf = settings.DATABASES.get("default", {})
        engine = str(db_conf.get("ENGINE", ""))
        if "sqlite3" not in engine:
            raise CommandError(
                "این فرمان فقط SQLite را پشتیبانی میکند "
                f"(ENGINE فعلی: {engine or 'نامشخص'})."
            )

        db_name = str(db_conf.get("NAME") or "")
        db_path = Path(db_name)
        if not db_path.is_absolute():
            db_path = Path(settings.BASE_DIR) / db_path
        in_memory = db_name == ":memory:" or db_name.startswith("file:")
        if not in_memory and not db_path.is_file():
            raise CommandError(
                f"فایل دیتابیس پیدا نشد: {db_path}. مسیر DB_NAME / NAME در "
                "تنظیمات را بررسی و دستور `python manage.py migrate --noinput` "
                "را اجرا کنید."
            )

        output = options.get("output")
        if output:
            final_path = Path(output).expanduser()
            if not final_path.is_absolute():
                final_path = Path(settings.BASE_DIR) / final_path
        else:
            final_path = (
                Path(settings.BASE_DIR)
                / DEFAULT_OUTPUT_DIRNAME
                / f"backup_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
            )
        final_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".tmp",
                dir=str(final_path.parent), delete=False,
            ) as tmp:
                tmp_path = Path(tmp.name)
                # dumpdata مستقیم به فایل موقت مینویسد تا stdout/journal لو نرود.
                call_command(
                    "dumpdata",
                    stdout=tmp,
                    format="json",
                    exclude=list(EXCLUDED_APPS),
                    indent=2,
                )

            if options.get("anonymize"):
                self._anonymize(tmp_path)

            # قبل از rename خروجی را اعتبارسنجی میکنیم تا فایل ناقص جایگزین نشود.
            with tmp_path.open("r", encoding="utf-8") as fh:
                rows = json.load(fh)
            if not isinstance(rows, list):
                raise ValueError("خروجی dumpdata یک لیست JSON معتبر نیست.")

            # best-effort: محدود کردن مجوز فایل (روی Windows فقط بیت writable است).
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass

            os.replace(tmp_path, final_path)  # اتمی: همان پوشه، همان فایل سیستم
            tmp_path = None
        except Exception as exc:
            if tmp_path is not None:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            detail = sanitize_error_text(f"{type(exc).__name__}: {exc}", 300)
            raise CommandError(
                "بکاپ ساخته نشد و هیچ فایل ناقصی باقی نماند: " + detail
            ) from exc

        label = "ناشناسشده" if options.get("anonymize") else "کامل (خصوصی)"
        self.stdout.write(self.style.SUCCESS(f"بکاپ {label} ذخیره شد: {final_path}"))
        self.stdout.write(self.style.WARNING(
            "یادآوری: ردیفهای سفارش شامل نام/تلفن مشتریاند. این فایل را خصوصی "
            "نگه دارید و آن را commit نکنید (پوشهٔ backups/ در .gitignore است)."
            )
        )

    @staticmethod
    def _anonymize(path: Path) -> None:
        """ماسک PII داخل فایل موقت؛ سپس همان فایل بازنویسی میشود."""
        with path.open("r", encoding="utf-8") as fh:
            rows = json.load(fh)
        for row in rows:
            model = row.get("model")
            fields = row.get("fields", {})
            if model == "restaurant.order":
                fields["customer_phone"] = mask_phone(fields.get("customer_phone"))
            for field in BLANK_FIELDS.get(model, ()):
                fields[field] = ""
        with path.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=2)
