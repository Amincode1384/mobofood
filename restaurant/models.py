import uuid, sys, traceback, logging

from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction as db_transaction
from django.utils import timezone

from restaurant.mission_statuses import (
    STATUS_PENDING,
    STATUS_QUEUED,
    STATUS_ASSIGNED,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    STATUS_CHOICES,
    can_transition,
    is_final,
    is_active,
    status_label,
    _validate_destination_table,
)

# SystemErrorLog در فایل جداگانهٔ error_logs.py ساخته شده و از همین‌جا
# معرفی می‌شود تا Django آن را به‌عنوان مدل اپ restaurant بشناسد و
# migration برایش بسازد.
from restaurant.error_logs import SystemErrorLog  # noqa: F401


# پیش‌فرض شناسهٔ مأموریت: MSN-XXXXXX (شش رقم ترتیبی)
# یکتایی در سطح دیتابیس نیز تضمین می‌شود.
MISSION_CODE_PREFIX = "MSN"

# محدودهٔ معتبر شمارهٔ میزها (باید با محدودهٔ QR Code و تولید QR هماهنگ باشد)
MIN_TABLE_NUMBER = 1
MAX_TABLE_NUMBER = 64


def _next_mission_code():
    """تولید شناسهٔ ترتیبی برای مأموریت جدید.

    از select_for_update برای قفل ردیف آخرین کد استفاده می‌شود
    تا در درخواست‌های هم‌زمان کد تکراری تولید نشود.
    """
    with db_transaction.atomic():
        last = Mission.objects.select_for_update().order_by("-mission_code").first()

        if last is None:
            return f"{MISSION_CODE_PREFIX}-{1:06d}"

        try:
            num = int(str(last.mission_code).split("-")[1]) + 1
        except (ValueError, IndexError):
            num = 1

        return f"{MISSION_CODE_PREFIX}-{num:06d}"


class Category(models.Model):
    name = models.CharField(max_length=100)
    icon = models.CharField(max_length=50, default="🍽")

    def __str__(self):
        return self.name


class Food(models.Model):
    category = models.ForeignKey(
        Category,
        on_delete=models.CASCADE,
        related_name="foods",
        null=True,
        blank=True,
    )
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    price = models.PositiveIntegerField()
    stock = models.PositiveIntegerField(default=0)

    # ---------------------------------------------------------
    # وضعیت فعال/غیرفعال بودن «مدیریتی» غذا
    # ---------------------------------------------------------
    # این فیلد با `stock` یکی نیست و نباید با آن قاطی شود:
    #   stock     → موجودی فیزیکی/تعداد قابل سفارش (اگر صفر باشد، «ناموجود»)
    #   is_active → اجازهٔ مدیر برای نمایش غذا در منوی مشتری
    # غذای غیرفعال حذف نمی‌شود؛ رکورد و سفارش‌های قبلی‌اش دست‌نخورده
    # می‌مانند و با یک کلیک دوباره فعال می‌شود.
    is_active = models.BooleanField(default=True, db_index=True)
    image = models.ImageField(upload_to="foods/", blank=True, null=True)

    def __str__(self):
        return self.name


class Order(models.Model):
    STATUS_CHOICES = [
        ("new", "جدید"),
        ("accepted", "تایید شده"),
        ("cooking", "در حال آماده سازی"),
        ("awaiting_confirmation", "در انتظار تأیید مشتری"),
        ("ready", "آماده"),
        ("sent", "ارسال شده"),
        ("done", "تحویل شده"),
        ("rejected", "رد شده"),
    ]

    table = models.CharField(max_length=20)

    # NEW: for the invoice only — not used for login/auth.
    customer_name = models.CharField(max_length=100, blank=True, default="")

    # NEW: optional — lets a returning customer be recognized by phone
    # even on a different device / after clearing browser storage.
    customer_phone = models.CharField(
        max_length=20, blank=True, default="", db_index=True
    )

    # NEW: comma-separated chair keys booked for this order, e.g.
    # "T3-top,T3-right" — lets us compute REAL seat occupancy instead
    # of a random/mocked layout on the frontend.
    seats = models.CharField(max_length=200, blank=True, default="")

    total = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=30, choices=STATUS_CHOICES, default="new"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cooking_started_at = models.DateTimeField(null=True, blank=True)

    # NEW: server-authoritative deadline for the prep/delivery countdown.
    # Clients compute their own countdown from this timestamp (immune to
    # clock drift / network lag) instead of the server ticking every second.
    eta_at = models.DateTimeField(null=True, blank=True)

    # NEW: set once the customer responds to the "did you receive it?"
    # prompt that appears when eta_at is reached. None = not answered yet.
    customer_confirmed = models.BooleanField(null=True, blank=True, default=None)

    # ---------------------------------------------------------
    # 🔐 اعتبارنامهٔ پیگیری سفارش (Order Tracking Token)
    # ---------------------------------------------------------
    # مشتری حساب کاربری ندارد؛ مالکیت سفارش با یک توکن اختصاصی اثبات
    # می‌شود که فقط یک‌بار به همان مشتری داده می‌شود.
    #   tracking_nonce      → مقدار تصادفی هر سفارش (پایهٔ مشتق‌سازی توکن)
    #   tracking_token_hash → sha256 توکن؛ توکن خام هیچ‌گاه ذخیره نمی‌شود
    # سفارشی که این دو خالی باشند (سفارش‌های قدیمی) از مسیر عمومی
    # پیگیری قابل مشاهده نیست.
    tracking_nonce = models.CharField(
        max_length=64, blank=True, default="", db_index=True
    )
    tracking_token_hash = models.CharField(
        max_length=64, blank=True, default=""
    )
    tracking_issued_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Order #{self.id}"


class OrderIdempotencyKey(models.Model):
    """
    کلید یکتای هر عملیات منطقی ثبت سفارش.

    - `key` با Unique Constraint تضمین می‌کند دو درخواست هم‌زمان با یک کلید
      نتوانند دو سفارش بسازند.
    - `fingerprint` اثر انگشت نرمال‌شدهٔ درخواست است تا همان کلید برای
      سفارش متفاوت قابل استفاده نباشد.
    - `order` پس از ساخت سفارش پر می‌شود؛ خالی بودن آن یعنی عملیات هنوز
      در حال پردازش است (یا تراکنش Rollback شده و کلید هم پاک شده است).
    """

    key = models.CharField(max_length=64, unique=True)
    fingerprint = models.CharField(max_length=64, blank=True, default="")
    order = models.ForeignKey(
        Order,
        on_delete=models.SET_NULL,
        related_name="idempotency_keys",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Idempotency {self.key} -> Order #{self.order_id}"


class OrderItem(models.Model):
    order = models.ForeignKey(
        Order, on_delete=models.CASCADE, related_name="items"
    )
    name = models.CharField(max_length=100)
    quantity = models.IntegerField()
    price = models.IntegerField()

    def __str__(self):
        return self.name


# =========================================================
# مأموریت ربات (Mission)
# =========================================================
# هر سفارش آماده‌شده یک مأموریت ربات می‌سازد.

# Mission و Order دو موجودیت مستقل با چرخهٔ عمر متفاوت هستند.
# وضعیت مأموریت کاملاً از وضعیت سفارش مستقل است.

class MissionQuerySet(models.QuerySet):
    """روش‌های فراموش‌نشدنی برای انتخاب و مدیریت مأموریت‌های ربات."""

    def active(self):
        """مأموریت‌هایی که هنوز در چرخهٔ اجرا فعال هستند."""
        return self.filter(status__in=active_status_set())

    def for_order(self, order):
        """مأموریت مرتبط با یک سفارش مشخص."""
        return self.filter(order=order)


class Mission(models.Model):
    """مأموریت ارسال سفارش توسط ربات گارسون."""
    class Meta:
        pass

    # شناسهٔ یکتای مأموریت (برای ارجاع و پیگیری)
    mission_uid = models.UUIDField(
        default=uuid.uuid4,
        unique=True,
        editable=False,
        db_index=True,
    )

    # شناسهٔ ترتیبی و خوانا برای نمایش و جست‌وجو (مثلاً MSN-000001)
    # یکتایی در سطح دیتابیس نیز تضمین شده است.
    mission_code = models.CharField(
        max_length=20,
        unique=True,
        editable=False,
        db_index=True,
    )

    # هر سفارش فقط یک مأموریت فعال دارد (OneToOne => Unique در DB)
    order = models.OneToOneField(
        Order,
        on_delete=models.CASCADE,
        related_name="mission",
    )

    # مقصد (شمارهٔ میز) — از اطلاعات معتبر سفارش استخراج می‌شود
    # و اعتبارسنجی می‌شود. فقط شمارهٔ میز معتبر (۱..۶۴) قابل ذخیره است.
    destination_table = models.CharField(
        max_length=20,
        validators=[
            _validate_destination_table,
        ],
    )

    # شناسهٔ ربات تخصیص‌یافته (در صورت تخصیص)
    robot_id = models.CharField(max_length=20, blank=True, default="")

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )

    # زمان‌های کلیدی چرخهٔ مأموریت (مستقل از زمان‌های سفارش)
    created_at = models.DateTimeField(auto_now_add=True)
    queued_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")

    # نوع خطا — برای پیگیری و گزارش‌سازی.
    error_type = models.CharField(
        max_length=50,
        blank=True,
        default="",
        choices=[
            ("invalid_destination", "مقصد نامعتبر"),
            ("order_missing", "سفارش مرتبط وجود ندارد"),
            ("duplicate_request", "درخواست تکراری"),
            ("robot_unavailable", "ربات در دسترس نیست"),
            ("robot_communication_failed", "ارتباط با ربات قطع/ناموفق"),
            ("robot_rejected", "ربات اجرای مأموریت را رد کرد"),
            ("timeout", "زمان اجرای مأموریت از حد مجاز عبور کرد"),
            ("database_error", "خطای دیتابیس در عملیات مأموریت"),
            ("other", "خطای دیگر"),
        ],
        help_text="نوع خطا برای پیگیری و گزارش‌سازی.",
    )

    class Meta:
        ordering = ["-created_at"]

    # مراجعات به وضعیت‌های ربات (مخزن واحد: restaurant.mission_statuses)
    STATUS_PENDING = STATUS_PENDING
    STATUS_QUEUED = STATUS_QUEUED
    STATUS_ASSIGNED = STATUS_ASSIGNED
    STATUS_IN_PROGRESS = STATUS_IN_PROGRESS
    STATUS_COMPLETED = STATUS_COMPLETED
    STATUS_FAILED = STATUS_FAILED
    STATUS_CANCELLED = STATUS_CANCELLED
    STATUS_CHOICES = STATUS_CHOICES

    def clean(self):
        """اعتبارسنجی: مقصد باید شمارهٔ میز معتبر باشد."""
        if self.destination_table is not None:
            try:
                table_num = int(str(self.destination_table).strip())
            except (ValueError, TypeError):
                raise ValidationError({
                    "destination_table":
                        f"شمارهٔ میز باید یک عدد صحیح باشد، نه «{self.destination_table!r}»."
                })
            if not (MIN_TABLE_NUMBER <= table_num <= MAX_TABLE_NUMBER):
                raise ValidationError({
                    "destination_table":
                        f"شمارهٔ میز باید بین {MIN_TABLE_NUMBER} و {MAX_TABLE_NUMBER} باشد، نه «{self.destination_table!r}»."
                })

    def save(self, *args, **kwargs):
        # اعتبارسنجی before ذخیره (حتی وقتی از create مستقیم استفاده شود)
        try:
            self.full_clean()
        except ValidationError:
            raise

        if not self.mission_code:
            # یک تلاش اتمیک: تولید کد ترتیبی و ذخیره.
            # اگر رقابت هم‌زمانی باعث برخورد با یکتا شد، IntegrityError
            # پرتاب می‌شود تا فراخواننده (مثلاً ready_order) تصمیم بگیرد.
            with db_transaction.atomic():
                self.mission_code = _next_mission_code()

        # ثبت تاریخ‌های مربوط به وضعیت (فقط یک بار، نه هر بار ذخیره)
        if self.status == STATUS_QUEUED and not self.queued_at:
            self.queued_at = timezone.now()
        elif self.status == STATUS_IN_PROGRESS and not self.started_at:
            self.started_at = timezone.now()
        elif self.status in (STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED):
            if not self.completed_at:
                self.completed_at = timezone.now()

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Mission {self.mission_code} ({self.mission_uid}) for Order #{self.order_id}"


class MissionManager(models.Manager.from_queryset(MissionQuerySet)):
    def contribute_to_class(self, cls, name):
        super().contribute_to_class(cls, name)
        self.name = name
        self.model = cls


MgrInstance = MissionManager()
try:
    MgrInstance.contribute_to_class(Mission, 'objects')
except Exception as e:
    import logging
    logging.getLogger(__name__).exception('contribute_to_class failed: %s', e)

Mission.objects = MgrInstance


def can_replace_mission_for_order(mission):
    """
    آیا می‌توان برای سفارشی که این مأموریت دارد، مأموریت جایگزین
    جدید ایجاد کرد؟

    سیاست پیش‌فرض:
      - اگر مأموریت در وضعیت final (completed/failed/cancelled) باشد،
        ایجاد مجاز است.
      - اگر مأموریت در وضعیت فعال (pending/queued/assigned/in_progress) باشد،
        ایجاد مأموریت تکراری ممنوع است.

    Returns:
        bool
    """
    if mission is None:
        return True

    return is_final(mission.status) if hasattr(mission, "status") else True


def _can_create_mission_for_order(order):
    """
    بررسی می‌کند که آیا می‌توان برای سفارش `order` مأموریت جدید ایجاد کرد.

    اگر:
      - مأموریتی برای این سفارش وجود ندارد → True
      - مأموریت وجود دارد اما در وضعیت final است → True (مأموریت جایگزین)
      - مأموریت وجود دارد و در وضعیت فعال است → False (ممنوع)

    Returns:
        tuple(bool, Mission | None): (ممکن‌است، مأموریت موجود یا None)
    """
    try:
        existing = Mission.objects.get(order=order)
    except Mission.DoesNotExist:
        return True, None

    return can_replace_mission_for_order(existing), existing
