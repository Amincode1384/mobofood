# -*- coding: utf-8 -*-
"""
گزارش خطاهای سیستم (System Error Reporting).

این ماژول «منبع واحد حقیقت» برای ثبت خطاهای مهم است:

  * مدل پایدار ``SystemErrorLog`` (در دیتابیس، قابل فیلتر در داشبورد)
  * پاک‌سازی متن خطا پیش از ذخیره (بدون شماره تلفن، توکن یا secret)
  * تابع مرکزی ``record_system_error(...)`` که از viewها، consumerهای
    Channels، middleware و handler لاگینگ قابل فراخوانی است.

قواعد مهم:
  - ثبت خطا هرگز نباید باعث شکست درخواست اصلی شود (fail-open):
    هر خطای داخلی این ماژول فقط با ``logger.exception`` ثبت می‌شود و
    ``False`` برمی‌گرداند.
  - تکراری‌ها (همان code + source + identifier در بازهٔ ۶۰ ثانیه)
    deduplicate می‌شوند تا لاگ انبوه نشود؛ تعداد تکرار در
    ``occurrences`` نگه داشته می‌شود.
"""

import logging
import re
import threading
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# ثابت‌ها و choices
# ---------------------------------------------------------

LEVEL_CHOICES = [
    ("INFO", "اطلاعاتی"),
    ("WARNING", "هشدار"),
    ("ERROR", "خطا"),
    ("CRITICAL", "بحرانی"),
]
LEVEL_VALUES = {value for value, _label in LEVEL_CHOICES}

SOURCE_CHOICES = [
    ("order", "چرخهٔ سفارش"),
    ("mission", "لایهٔ مأموریت ربات"),
    ("api", "API"),
    ("websocket", "WebSocket"),
    ("view", "صفحه/View"),
    ("other", "سایر"),
]
SOURCE_VALUES = {value for value, _label in SOURCE_CHOICES}

# بازهٔ deduplicate: همان code+source+identifier در این بازه فقط یک ردیف.
DEDUP_WINDOW_SECONDS = 60

# سقف طول پیام ذخیره‌شده (جلوگیری از پرشدن دیتابیس با متن‌های طولانی).
MAX_MESSAGE_LENGTH = 1000
MAX_CODE_LENGTH = 64
MAX_PATH_LENGTH = 300

# کلیدهایی که مقدارشان نباید هرگز ذخیره شود.
_SECRET_SETTING_NAMES = ("SECRET_KEY", "ROBOT_API_TOKEN", "ORDER_TRACKING_SECRET")

# الگوی key=value حساس (token=...، password: ... و ...) در هر متنی.
_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(token|password|passwd|secret|api[_-]?key|authorization)"
    r"(\s*[=:]\s*)([^\s&\"']+)"
)

# الگوی شماره تلفن (اقلیم ایران: 09xxxxxxxxx / +98... و معادل‌های بین‌المللی).
_PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{7,}\d")


# ---------------------------------------------------------
# مدل
# ---------------------------------------------------------

class SystemErrorLog(models.Model):
    """یک خطای مهم سیستم، ثبت‌شده از چرخهٔ سفارش، مأموریت، API یا WebSocket."""

    level = models.CharField(
        max_length=10,
        choices=LEVEL_CHOICES,
        default="ERROR",
        db_index=True,
        help_text="شدت خطا (INFO/WARNING/ERROR/CRITICAL).",
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default="other",
        db_index=True,
        help_text="لایه‌ای که خطا در آن رخ داده است.",
    )

    code = models.CharField(
        max_length=MAX_CODE_LENGTH,
        blank=True,
        default="",
        db_index=True,
        help_text="کوتاه، پایدار و انگلیسی؛ مثال: robot_timeout.",
    )

    message = models.TextField(
        blank=True,
        default="",
        help_text="پیام خوانا، پاک‌سازی‌شده از داده‌های حساس.",
    )

    # شناسه‌های ارتباطی (رشته؛ مدل Robot وجود ندارد).
    order_uid = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="شناسهٔ سفارش مرتبط (در صورت وجود).",
    )
    mission_uid = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="mission_uid مرتبط (در صورت وجود).",
    )
    robot_id = models.CharField(
        max_length=64, blank=True, default="", db_index=True,
        help_text="شناسهٔ ربات (رشته)، در صورت وجود.",
    )
    path = models.CharField(
        max_length=MAX_PATH_LENGTH, blank=True, default="",
        help_text="مسیر درخواست یا ماژولی که خطا در آن رخ داده.",
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # برای deduplicate: تعداد تکرار همین خطا در بازهٔ ۶۰ ثانیه و آخرین زمان.
    occurrences = models.PositiveIntegerField(
        default=1,
        help_text="تعداد تکرار همین خطا (deduplicate‌شده).",
    )
    last_seen_at = models.DateTimeField(
        null=True, blank=True,
        help_text="آخرین باری که همین خطا دوباره دیده شد.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"[{self.level}] {self.source}:{self.code} @ {self.created_at}"


# ---------------------------------------------------------
# پاک‌سازی داده‌های حساس
# ---------------------------------------------------------

def sanitize_error_text(text, max_length=MAX_MESSAGE_LENGTH):
    """
    پاک‌سازی متن خطا پیش از ذخیره/نمایش.

    - مقدار واقعی SECRET_KEY / ROBOT_API_TOKEN / ORDER_TRACKING_SECRET
      → ``[REDACTED]``
    - الگوی ``token=...`` و هم‌خانواده‌ها → مقدار حذف می‌شود
    - شماره تلفن (۹ رقم به بالا) → ``[PHONE]``
    - برش به سقف طول
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # ۱) مقادیر شناخته‌شدهٔ secret از تنظیمات
    for name in _SECRET_SETTING_NAMES:
        value = getattr(settings, name, None)
        if value and len(str(value)) >= 8:
            text = text.replace(str(value), "[REDACTED]")

    # ۲) الگوی key=value حساس
    text = _SENSITIVE_KV_RE.sub(rf"\1\2[REDACTED]", text)

    # ۳) شماره تلفن
    text = _PHONE_RE.sub("[PHONE]", text)

    # ۴) سقف طول
    if len(text) > max_length:
        text = text[: max_length - 1] + "…"

    return text.strip()


def _message_from_exc_info(exc_info):
    """خلاصهٔ امن از استثنا: فقط نام کلاس + پیام (بدون traceback کامل)."""
    if not exc_info:
        return ""
    etype, evalue, _tb = exc_info
    name = getattr(etype, "__name__", str(etype))
    return f"{name}: {evalue}"


# ---------------------------------------------------------
# identifier برای deduplicate
# ---------------------------------------------------------

def _identifier_for(order_uid, mission_uid, robot_id, path):
    """
    شناسهٔ یکتای خطا برای deduplicate (اولویت با مهم‌ترین شناسه).

    Returns:
        (field_name, value) — field_name یکی از mission_uid/order_uid/
        robot_id/path است.
    """
    if mission_uid:
        return "mission_uid", str(mission_uid)[:64]
    if order_uid:
        return "order_uid", str(order_uid)[:64]
    if robot_id:
        return "robot_id", str(robot_id)[:64]
    return "path", (path or "")[:MAX_PATH_LENGTH]


# ---------------------------------------------------------
# تابع مرکزی
# ---------------------------------------------------------

# گارد re-entrancy: اگر logging handler داخل همین فراخوانی فعال شود،
# دوباره وارد نشویم (جلوگیری از حلقهٔ بی‌نهایت).
_recording = threading.local()


def record_system_error(
    *,
    level="ERROR",
    source="other",
    code="",
    message="",
    order_uid=None,
    mission_uid=None,
    robot_id=None,
    path=None,
    details="",
    exc_info=None,
):
    """
    ثبت یک خطای مهم در ``SystemErrorLog`` — منبع مرکزی هر چهار لایه.

    Args:
        level: یکی از INFO/WARNING/ERROR/CRITICAL (نامعتبر → ERROR).
        source: یکی از order/mission/api/websocket/view/other (نامعتبر → other).
        code: کد خطای کوتاه و پایدار (مثلاً ``robot_timeout``).
        message: پیام خوانا؛ قبل از ذخیره sanitize می‌شود.
        order_uid/mission_uid/robot_id: شناسه‌های ارتباطی (اختیاری).
        path: مسیر درخواست یا ماژول (اختیاری).
        details: جزئیات اضافهٔ کوتاه (اختیاری، sanitize می‌شود).
        exc_info: سه‌گانهٔ ``sys.exc_info()``؛ اگر message خالی باشد،
            خلاصهٔ امن استثنا به‌کار می‌رود.

    Returns:
        bool: True اگر ردیف ثبت (یا با شمارش تکرار به‌روز) شد،
        False اگر به هر دلیلی ثبت نشد (fail-open — هرگز raise نمی‌دهد).
    """
    # گارد re-entrancy باید اولین خط باشد تا هیچ حلقه‌ای شکل نگیرد.
    if getattr(_recording, "active", False):
        return False
    _recording.active = True

    try:
        if level not in LEVEL_VALUES:
            level = "ERROR"
        if source not in SOURCE_VALUES:
            source = "other"

        code = sanitize_error_text(code, MAX_CODE_LENGTH) or "unspecified"

        text = message or _message_from_exc_info(exc_info)
        if details:
            text = f"{text} | {details}" if text else details
        message = sanitize_error_text(text)
        if not message:
            message = code

        field, identifier = _identifier_for(order_uid, mission_uid, robot_id, path)

        cutoff = timezone.now() - timedelta(seconds=DEDUP_WINDOW_SECONDS)

        # --- deduplicate: همان code+source+identifier در بازهٔ ۶۰ ثانیه
        existing = (
            SystemErrorLog.objects
            .filter(
                code=code,
                source=source,
                created_at__gte=cutoff,
                **{field: identifier},
            )
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            SystemErrorLog.objects.filter(pk=existing.pk).update(
                occurrences=models.F("occurrences") + 1,
                last_seen_at=timezone.now(),
            )
            return True

        SystemErrorLog.objects.create(
            level=level,
            source=source,
            code=code,
            message=message,
            order_uid=str(order_uid or "")[:64],
            mission_uid=str(mission_uid or "")[:64],
            robot_id=str(robot_id or "")[:64],
            path=(path or "")[:MAX_PATH_LENGTH],
            last_seen_at=timezone.now(),
        )
        return True

    except Exception:
        # fail-open: ثبت خطا نباید هرگز درخواست اصلی را بشکند.
        # (این logger به handler دیتابیس وصل نیست؛ فقط logging استاندارد.)
        logger.exception(
            "record_system_error: ذخیره در SystemErrorLog ناموفق بود "
            "(fail-open to standard logging)."
        )
        return False
    finally:
        _recording.active = False
