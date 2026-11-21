"""
منبع واحد حقیقت برای چرخهٔ وضعیت سفارش.

قبل از این ماژول، وضعیت‌ها در مدل، چند View، `order_tracking.html` و
`customer.js` جداگانه تعریف شده بودند و با هم نمی‌خواندند (مثلاً `ready`
نه در مراحل پیگیری بود و نه درصد مشخصی داشت و به‌اشتباه ۱۰۰٪ نشان داده
می‌شد). اکنون همهٔ برچسب‌های مشتری، مراحل پیگیری، درصد پیشرفت و انتقال‌های
مجاز فقط از همین‌جا می‌آیند.

چرخهٔ واقعی پروژه:

    new ──accept──> cooking ──ready──> ready ──send──> sent
                        │                                │
                        │ (اتمام تایمر آماده‌سازی)        │
                        └──> awaiting_confirmation <─────┘
                                    │
                     تأیید مشتری/آشپزخانه ──> done

    new ──reject──> rejected   (خارج از مسیر پیشرفت)

معنای دقیق:
    ready  = غذا آماده شده است، ولی هنوز تحویل نشده است.
    sent   = سفارش با ربات به سمت میز حرکت کرده است، ولی تحویل تأیید نشده.
    awaiting_confirmation = تحویل انجام شده و منتظر تأیید دریافت هستیم.
    done   = تحویل تأیید شده / سفارش تکمیل شده است.

هیچ‌گاه «آماده شدن غذا» به معنی «تحویل» نیست.
"""

from django.utils import timezone

# ---------------------------------------------------------
# کلیدهای وضعیت (باید با Order.STATUS_CHOICES یکی باشند)
# ---------------------------------------------------------

STATUS_NEW = "new"
STATUS_ACCEPTED = "accepted"          # پذیرش (برای سازگاری با دادهٔ قدیمی)
STATUS_COOKING = "cooking"
STATUS_READY = "ready"
STATUS_SENT = "sent"
STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"
STATUS_DONE = "done"
STATUS_REJECTED = "rejected"

STATUS_KEYS = [
    STATUS_NEW,
    STATUS_ACCEPTED,
    STATUS_COOKING,
    STATUS_AWAITING_CONFIRMATION,
    STATUS_READY,
    STATUS_SENT,
    STATUS_DONE,
    STATUS_REJECTED,
]

CANCELLED_STATUSES = frozenset({STATUS_REJECTED})

UNKNOWN_STATUS_LABEL = "وضعیت سفارش در حال بررسی است"

# ---------------------------------------------------------
# برچسب‌های مشتری — تنها منبع متن‌های صفحهٔ پیگیری
# ---------------------------------------------------------

STATUS_LABELS = {
    STATUS_NEW: "سفارش شما ثبت شده است",
    STATUS_ACCEPTED: "سفارش شما تأیید شده است",
    STATUS_COOKING: "غذای شما در حال آماده‌سازی است",
    STATUS_READY: "غذای شما آماده تحویل است",
    STATUS_SENT: "سفارش شما در حال ارسال است",
    STATUS_AWAITING_CONFIRMATION: "در انتظار تأیید تحویل",
    STATUS_DONE: "غذا به شما تحویل داده شده است",
    STATUS_REJECTED: "سفارش شما لغو شده است",
}

# ---------------------------------------------------------
# مراحل قابل نمایش به مشتری (به ترتیب چرخه)
# ---------------------------------------------------------

TRACKING_STAGES = [
    {
        "key": STATUS_NEW,
        "label": "سفارش شما ثبت شده است",
        "description": "سفارش شما با موفقیت دریافت شد",
        "progress": 10,
    },
    {
        "key": STATUS_COOKING,
        "label": "غذای شما در حال آماده‌سازی است",
        "description": "آشپزخانه سفارش را تأیید کرده و در حال آماده‌سازی است",
        "progress": 40,
    },
    {
        "key": STATUS_READY,
        "label": "غذای شما آماده تحویل است",
        "description": "غذا آماده است، ولی هنوز به میز شما تحویل نشده",
        "progress": 60,
    },
    {
        "key": STATUS_SENT,
        "label": "سفارش شما در حال ارسال است",
        "description": "ربات سفارش را به سمت میز شما می‌برد",
        "progress": 75,
    },
    {
        "key": STATUS_AWAITING_CONFIRMATION,
        "label": "در انتظار تأیید تحویل",
        "description": "لطفاً دریافت سفارش را تأیید کنید",
        "progress": 90,
    },
    {
        "key": STATUS_DONE,
        "label": "غذا به شما تحویل داده شده است",
        "description": "نوش جان!",
        "progress": 100,
    },
]

TRACKING_STAGE_KEYS = [stage["key"] for stage in TRACKING_STAGES]

# ---------------------------------------------------------
# انتقال‌های مجاز
# ---------------------------------------------------------

ALLOWED_TRANSITIONS = {
    STATUS_NEW: {STATUS_COOKING, STATUS_ACCEPTED, STATUS_REJECTED},
    STATUS_ACCEPTED: {STATUS_COOKING, STATUS_REJECTED},
    STATUS_COOKING: {STATUS_READY, STATUS_AWAITING_CONFIRMATION},
    STATUS_READY: {STATUS_SENT, STATUS_COOKING},
    STATUS_SENT: {STATUS_AWAITING_CONFIRMATION},
    STATUS_AWAITING_CONFIRMATION: {STATUS_DONE, STATUS_COOKING},
    STATUS_DONE: set(),
    STATUS_REJECTED: set(),
}


def can_transition(current, target):
    """
    آیا تغییر وضعیت از `current` به `target` مطابق چرخه مجاز است؟

    وضعیت ناشناخته هیچ انتقالی ندارد (fail-closed).
    """
    return target in ALLOWED_TRANSITIONS.get(current, set())


def is_cancelled(status):
    return status in CANCELLED_STATUSES


def is_known(status):
    return status in STATUS_LABELS


def is_unknown(status):
    return not is_known(status)


def status_label(status):
    """متن قابل نمایش به مشتری برای یک وضعیت (با fallback امن)."""
    return STATUS_LABELS.get(status, UNKNOWN_STATUS_LABEL)


def stage_index(status):
    """
    ایندکس مرحلهٔ فعلی در `TRACKING_STAGES`.

    `accepted` معادل مرحلهٔ آماده‌سازی در نظر گرفته می‌شود و وضعیت‌های
    لغوشده/ناشناخته ایندکس منفی دارند (هیچ مرحلهٔ فعالی ندارند).
    """
    if status == STATUS_ACCEPTED:
        return TRACKING_STAGE_KEYS.index(STATUS_COOKING)

    if status in TRACKING_STAGE_KEYS:
        return TRACKING_STAGE_KEYS.index(status)

    return -1


def steps_for(status):
    """
    مراحل پیگیری برای یک وضعیت.

    هر مرحله: key / label / description / state ∈ {done, current, pending}

    - وضعیت لغوشده: هیچ مرحله‌ای current نیست تا به‌عنوان پیشرفت عادی
      نمایش داده نشود.
    - وضعیت ناشناخته: همان رفتار (بدون پیشرفت جعلی).
    - `done`: همهٔ مراحل done هستند (سفارش تکمیل شده).
    """
    index = stage_index(status)
    no_progress = is_cancelled(status) or is_unknown(status) or index < 0

    steps = []
    for position, stage in enumerate(TRACKING_STAGES):
        if no_progress:
            state = "pending"
        elif position < index:
            state = "done"
        elif position == index:
            state = "done" if status == STATUS_DONE else "current"
        else:
            state = "pending"

        steps.append(
            {
                "key": stage["key"],
                "label": stage["label"],
                "description": stage["description"],
                "state": state,
            }
        )

    return steps


def _cooking_progress(order, now=None):
    """
    پیشرفت در مرحلهٔ آماده‌سازی: از ۱۰٪ شروع و حداکثر به ۴۰٪ می‌رسد.

    هرگز به ۱۰۰٪ نمی‌رسد، چون آماده شدن/گذشت زمان به معنی تحویل نیست.
    """
    now = now or timezone.now()
    started = order.cooking_started_at

    if not started:
        return 25

    if order.eta_at:
        total = (order.eta_at - started).total_seconds()
    else:
        total = 5 * 60

    total = max(total, 60.0)
    elapsed = max((now - started).total_seconds(), 0.0)
    ratio = min(elapsed / total, 1.0)

    return int(10 + (40 - 10) * ratio)


def progress_for(order, now=None):
    """
    درصد پیشرفت بر اساس وضعیت واقعی سفارش (منبع: دیتابیس).

    - `ready` هرگز ۱۰۰٪ نیست (۶۰٪) و تحویل را تکمیل نشان نمی‌دهد.
    - `done` تنها وضعیتی است که ۱۰۰٪ می‌شود.
    - لغوشده/ناشناخته پیشرفتی ندارند.
    """
    status = order.status

    if is_cancelled(status) or is_unknown(status):
        return 0

    if status == STATUS_COOKING:
        return _cooking_progress(order, now=now)

    if status == STATUS_ACCEPTED:
        return 25

    for stage in TRACKING_STAGES:
        if stage["key"] == status:
            return stage["progress"]

    return 0


def remaining_seconds_for(order, now=None):
    """
    ثانیهٔ باقی‌ماندهٔ نمایشی.

    فقط در مرحلهٔ آماده‌سازی معنا دارد؛ برای `ready`/`sent` عدد ساختگی
    برنمی‌گردانیم (۰ = نامشخص) تا «زمان تحویل» جعل نشود.
    """
    if order.status == STATUS_COOKING and order.eta_at:
        now = now or timezone.now()
        return max(int((order.eta_at - now).total_seconds()), 0)

    return 0
