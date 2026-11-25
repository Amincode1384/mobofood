"""
منبع واحد حقیقت برای چرخهٔ وضعیت مأموریت ربات.

قبل از این، وضعیت‌های مأموریت در مدل és در `update_mission_status` تعریف می‌شدند
و هیچ هیچ تحمیلی بین آن‌ها وجود نداشت. اکنون همهٔ انتقال‌های مجاز، برگه‌های
نتیجه و قوانین لغو فقط از همین‌جا می‌آیند.

چرخهٔ مفهومی مأموریت ربات:

    pending ──► queued ──► assigned ──► in_progress ──► completed
       │                      │
       └──────────────────────┼──► cancelled
                              │
                              └──► failed

معانی دقیق:
    pending        مأموریت ایجاد شده، هنوز وارد صف اجرا نشده.
    queued         مأموریت در صف انتظار برای تخصیص به ربات است.
    assigned       ربات این مأموریت را دریافت کرده و در حال پیش‌روی به میز است.
    in_progress    ربات در حال اجرای مأموریت است (به سمت میز حرکت یا در حال تحویل).
    completed      مأموریت با موفقیت به پایان رسیده است.
    failed         مأموریت به دلیل خطا ناموفق شده و نیاز به بررسی/جایگزین دارد.
    cancelled      مأموریت توسط نرم‌افزار لغو شده است.
    completed      مأموریت با موفقیت به پایان رسیده (تحویل انجام شد).
    failed         مأموریت به دلیل خطا ناموفق شده (نیاز به بررسی/جایگزین).
    cancelled      مأموریت توسط نرم‌افزار لغو شده (سلب اعتبار).

توجه:
    - «آماده شدن غذا» (Order.ready) با «اجرای مأموریت» (Mission) متفاوت است.
    - «ارسال فرمان به ربات» با «تحویل واقعی» متفاوت است.
    - completed در این مدل به معنای پایان موفقیت‌آمیز فرآیند ربات است،
      نه صرفاً ارسال فرمان.
"""

from django.core.exceptions import ValidationError

from .table_config import (
    FIRST_TABLE_NUMBER as MIN_TABLE_NUMBER,
    get_max_table_number,
)

# نام‌های قدیمی برای سازگاری حفظ شدهاند، ولی مقدارشان از FLOOR_TOTAL_TABLES میآید.
MAX_TABLE_NUMBER = get_max_table_number()


# ---------------------------------------------------------
# کلیدهای وضعیت (باید با Mission.STATUS_* یکی باشند)
# ---------------------------------------------------------
STATUS_PENDING = "pending"
STATUS_QUEUED = "queued"
STATUS_ASSIGNED = "assigned"
STATUS_IN_PROGRESS = "in_progress"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

STATUS_KEYS = [
    STATUS_PENDING,
    STATUS_QUEUED,
    STATUS_ASSIGNED,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
]

# وضعیت‌هایی که به معنای «از جریان خارج» هستند
FINAL_STATUSES = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})

# ---------------------------------------------------------
# برچسب‌های فارسی — تنها منبع متن‌های نمایشی
# ---------------------------------------------------------
STATUS_LABELS = {
    STATUS_PENDING: "در انتظار اجرا",
    STATUS_QUEUED: "در صف انتظار",
    STATUS_ASSIGNED: "تخصیص‌یافته به ربات",
    STATUS_IN_PROGRESS: "در حال اجرا",
    STATUS_COMPLETED: "با موفقیت انجام شد",
    STATUS_FAILED: "با خطا مواجه شد",
    STATUS_CANCELLED: "لغو شد",
}

# ---------------------------------------------------------
# انتقال‌های مجاز
# ---------------------------------------------------------

ALLOWED_TRANSITIONS = {
    # A robot may claim a freshly created mission directly (no separate
    # queue service exists). Keep `queued` as an optional intermediate step.
    STATUS_PENDING: {
        STATUS_QUEUED,
        STATUS_ASSIGNED,
        STATUS_IN_PROGRESS,
        STATUS_COMPLETED,
        STATUS_CANCELLED,
    },
    STATUS_QUEUED: {STATUS_ASSIGNED, STATUS_CANCELLED},
    STATUS_ASSIGNED: {STATUS_IN_PROGRESS, STATUS_CANCELLED},
    STATUS_IN_PROGRESS: {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED},
    STATUS_COMPLETED: set(),
    STATUS_FAILED: set(),
    STATUS_CANCELLED: set(),
}

STATUS_CHOICES = [(s, STATUS_LABELS[s]) for s in STATUS_KEYS]

# ---------------------------------------------------------
# سیاست جایگزین‌سازی مأموریت
# ---------------------------------------------------------

# آیا می‌توان مأموریت جدید برای یک سفارش که مأموریت قبلی دارد ایجاد کرد؟
# ش 관계기업ت:
#   True  → امکان‌پذیر است (fcาประโยชน์ تقدیم می‌شود)
#   False → ممنوع است
#
# پیش‌فرض:
#   - اگر مأموریت فعلاً در وضعیت‌های فعال (pending/queued/assigned/in_progress)
#     باشد، ایجاد مأموریت تکراری ممنوع است.
#   - اگر مأموریت در وضعیت final (completed/failed/cancelled) باشد، ایجاد
#     مأموریت جایگزین مجاز است (با ایجاد مأموریت Bacon با شناسهٔ جدید).
#
# این سیاست در `views.ready_order` اعمال می‌شود (می‌پرد چک می‌کند که آیا
# می‌توان مأموریت جدید برای این سفارش ایجاد کرد).

CAN_REPLACE_MISSION_IF_FINAL = True


def can_transition(current, target):
    """آیا تغییر وضعیت از `current` به `target` مطابق چرخهٔ مجاز است؟"""
    return target in ALLOWED_TRANSITIONS.get(current, set())



def is_final(status):
    """وضعیت نهایی است؛ برای لغو/تغییر بعدی ممنوع است."""
    return status in FINAL_STATUSES


def is_active(status):
    """وضعیت‌هایی که هنوز در چرخهٔ اجرا فعالند."""
    return status not in FINAL_STATUSES


def status_label(status):
    """متن قابل نمایش برای یک وضعیت (با fallback امن)."""
    return STATUS_LABELS.get(status, status)


def transition_message(current, target):
    """
    پیام دقیق برای گزارش تغییر وضعیت (برای لاگ‌ها و احتمالاً پیام‌های ارسالی به ربات).
    returns: str | None
    """
    if target == STATUS_COMPLETED:
        return "مأموریت با موفقیت به پایان رسیده است."
    if target == STATUS_FAILED:
        return "مأموریت با خطا مواجه شده است."
    if target == STATUS_CANCELLED:
        return "مأموریت لغو شده است."
    if target == STATUS_ASSIGNED:
        return "مأموریت به ربات اختصاص یافته است."
    if target == STATUS_IN_PROGRESS:
        return "اجرای مأموریت آغاز شده است."
    if target == STATUS_QUEUED:
        return "مأموریت وارد صف انتظار شده است."
    if target == STATUS_PENDING:
        return "مأموریت ایجاد شده و در انتظار ورود به صف است."
    return None


def mission_final_status_estimates():
    """
    فهرست وضعیت‌های نهایی که نشان‌دهندهٔ پایان چرخهٔ مأموریت هستند.
    برای فیلترهای UI و آمار.
    """
    return {
        "completed": STATUS_COMPLETED,
        "failed": STATUS_FAILED,
        "cancelled": STATUS_CANCELLED,
    }


def active_status_set():
    """مجموعهٔ وضعیت‌هایی که نیاز به مدیریت صف و تخصیص دارند."""
    return set(STATUS_KEYS) - FINAL_STATUSES


# ---------------------------------------------------------
# اعتبارسنجی مقصد مأموریت (مولفهٔ مشترک با models)
# ---------------------------------------------------------

def _validate_destination_table(value):
    """اعتبارسنجی شمارهٔ میز مقصد مأموریت.

    - باید یک عدد صحیح باشد.
    - باید در محدودهٔ معتبر میزها (۱ .. FLOOR_TOTAL_TABLES) قرار بگیرد.
    """
    if value is None:
        return
    try:
        table_num = int(str(value).strip())
    except (ValueError, TypeError):
        raise ValidationError(
            f"شمارهٔ میز مقصد باید یک عدد صحیح باشد، نه «{value!r}»."
        )
    max_table = get_max_table_number()
    if not (MIN_TABLE_NUMBER <= table_num <= max_table):
        raise ValidationError(
            f"شمارهٔ میز مقصد باید بین {MIN_TABLE_NUMBER} و {max_table} باشد، نه «{value!r}»."
        )








