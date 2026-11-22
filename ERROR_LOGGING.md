# System Error Reporting — گزارش خطاهای سیستم

قابلیت مشاهده‌پذیری (observability) پروژهٔ موبو فود / EnjiNex:
هر استثنای مهم در چرخهٔ سفارش، لایهٔ مأموریت ربات، APIها و WebSocket
در مدل پایدار `SystemErrorLog` ذخیره و در داشبورد مدیریت قابل فیلتر است.

## اجزا

| جزء | فایل | نقش |
|---|---|---|
| مدل + تابع مرکزی | `restaurant/error_logs.py` | `SystemErrorLog`، `sanitize_error_text()`، `record_system_error()` |
| middleware + handler | `restaurant/error_logging.py` | `SystemErrorReportingMiddleware`، `SystemErrorDatabaseHandler` |
| migration | `restaurant/migrations/0014_systemerrorlog.py` | ساخت جدول |
| داشبورد | `restaurant/views.py::system_errors` + `templates/system_errors.html` | جدول RTL با فیلتر (مسیر `/dashboard/errors/`) |
| ادمین | `restaurant/admin.py::SystemErrorLogAdmin` | فقط‌خواندنی، فیلتر و جست‌وجو |
| تنظیمات | `RestaurantRobot/settings.py` | `MIDDLEWARE` (یک ردیف) + `LOGGING` |
| تست‌ها | `restaurant/tests_error_logs.py` | ۱۷ تست |

## فراخوانی از هر لایه

```python
from restaurant.error_logs import record_system_error

record_system_error(
    level="ERROR",            # INFO/WARNING/ERROR/CRITICAL
    source="mission",         # order/mission/api/websocket/view/other
    code="robot_timeout",     # کد کوتاه پایدار
    message=str(exc),         # پیش از ذخیره sanitize می‌شود
    mission_uid=str(m.mission_uid),
    robot_id=m.robot_id,
    path="restaurant.mission_dispatch",
)
```

نکات:
- **fail-open:** ثبت خطا هرگز raise نمی‌دهد؛ در صورت خرابی دیتابیس فقط
  با `logger.exception` در logging استاندارد ادامه می‌دهد.
- **sanitize:** شماره تلفن → `[PHONE]`؛ مقدار `SECRET_KEY`،
  `ROBOT_API_TOKEN`، `ORDER_TRACKING_SECRET` و الگوی `token=...` → `[REDACTED]`.
- **deduplicate:** همان `code + source + identifier` (mission_uid یا
  order_uid یا robot_id یا path) در بازهٔ ۶۰ ثانیه فقط یک ردیف می‌سازد و
  تعداد در `occurrences` می‌نشیند.

## پوشش لایه‌ها

- **سفارش/API/View:** پاسخ‌های 5xx و استثناهای پردازش‌نشده توسط middleware
  با source بر اساس مسیر (`/api/orders…` → order، `/api/missions…` → mission،
  بقیهٔ `/api/` → api، بقیه → view) ثبت می‌شود.
- **مأموریت:** ۴ مسیر شکست transport در `mission_dispatch.py`
  (`robot_timeout`، `robot_disconnected`، `robot_rejected`، `transport_error`).
- **WebSocket:** خطاهای consumer ربات (`missing_mission_id`,
  `mission_not_found`, `robot_scope_violation`, `wrong_command_id`,
  `duplicate_response`, `websocket_receive_failed`) و کانال آشپزخانه
  (`kitchen_receive_failed`).
- **لاگینگ:** هر رکورد WARNING+ از لاگر `restaurant` با handler متصل به
  دیتابیس (کد `log_warning` و …) ثبت می‌شود.

## اجرا

```bash
python manage.py migrate          # اعمال 0014_systemerrorlog
python manage.py test restaurant  # کل سری (۴۲۳ تست)
```

داشبورد: `/dashboard/errors/` (فقط `is_staff`)؛ فیلترها: level، source،
order_uid، mission_uid، robot_id، بازهٔ تاریخ.

## ملاحظات production

- `DEBUG=0`، `ALLOWED_HOSTS` صریح، `DJANGO_SECRET_KEY` از محیط (همچنان
  الزامیِ قبلی).
- پاسخ API همچنان جزئیات internal برنمی‌گرداند؛ پیام‌های این جدول هم
  پیش از ذخیره پاک‌سازی می‌شوند.
