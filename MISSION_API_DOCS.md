# EnjiNex — مستند فنی API ارسال مأموریت به ربات گارسون

نسخه قرارداد Movement Command: `1.0` (تأییدشده توسط مدیر عامل).

---

## ۱. Endpoint

| مورد | مقدار |
|---|---|
| Endpoint | `POST /api/missions/dispatch/` |
| HTTP Method | `POST` |
| احراز هویت | Session/auth کارکنان (`is_staff`) — همان `_kitchen_api_required` |
| CSRF | لازم است (درخواست از پنل کارکنان) |

---

## ۲. ساختار درخواست

```json
{
  "mission_uid": "3f7c1b9e-...",
  "robot_id": "RBOT-01",
  "parameters": { "speed": "normal" }
}
```

- `mission_uid` **یا** `mission_code` (مثلاً `MSN-000001`) الزامی است.
- `robot_id` اختیاری است؛ در صورت وجود باید با `mission.robot_id` همخوانی داشته باشد.
- تمام اطلاعات مهم (مقصد، سفارش) **از دیتابیس** استخراج میشوند، نه از درخواست.

---

## ۳. ساختار پاسخ موفق (HTTP 200)

```json
{
  "success": true,
  "idempotent_replay": false,
  "valid": true,
  "mission_found": true,
  "robot_valid": true,
  "command_built": true,
  "command_sent": true,
  "command_id": "9b1c...",
  "robot_id": "RBOT-01",
  "destination_id": "TABLE_04",
  "received": true,
  "accepted": true,
  "command_status": "accepted",
  "mission_status": "assigned",
  "message": "فرمان حرکت برای ربات ارسال شد."
}
```

---

## ۴. ساختار پاسخ ناموفق

```json
{ "success": false, "code": "mission_not_found", "error": "مأموریت پیدا نشد." }
```

هیچ Stack Trace یا جزئیات خام Exception در پاسخ گنجانده نمیشود.

---

## ۵. کدهای وضعیت HTTP

| وضعیت | معنی |
|---|---|
| 200 | ارسال موفق (یا Idempotent replay) |
| 400 | ورودی/ساختار نامعتبر |
| 403 | عدم احراز هویت / مجوز |
| 404 | مأموریت ناموجود |
| 409 | وضعیت غیرقابلارسال، ربات ناموجود/ردشده، عدم تطابق robot_id |
| 503 | قطع ارتباط / خطای لایه ارتباطی |
| 504 | Timeout (وضعیت فرمان نامشخص) |

---

## ۶. فهرست کدهای خطا

| کد | توضیح |
|---|---|
| `missing_mission_id` | شناسه مأموریت ارسال نشده |
| `mission_not_found` | مأموریت پیدا نشد |
| `mission_not_dispatchable` | وضعیت فعلی مأموریت قابلارسال نیست |
| `robot_unavailable` | ربات مقصد تعیین نشده |
| `robot_id_mismatch` | robot_id درخواستی با مأموریت همخوانی ندارد |
| `invalid_destination` | شماره میز مقصد نامعتبر |
| `invalid_command` | ساختار فرمان نامعتبر |
| `unsupported_protocol_version` | نسخه قرارداد پشتیبانی نشد |
| `robot_timeout` | پاسخی در Timeout دریافت نشد (ارسال مجدد خودکار نشد) |
| `robot_disconnected` | ارتباط با ربات قطع است |
| `robot_rejected` | ربات فرمان را رد کرد |
| `transport_error` | خطای عمومی لایه ارتباطی |

---

## ۷. قرارداد Movement Command

```json
{
  "protocol_version": "1.0",
  "command_id": "<uuid>",
  "robot_id": "RBOT-01",
  "command_type": "MOVE_TO_TABLE",
  "mission_id": "<mission_uid>",
  "destination_id": "TABLE_04",
  "created_at": "2026-11-20T12:00:00+00:00",
  "parameters": {}
}
```

نگاشت مقصد تکمحلی در `restaurant/movement_command.py`:

```
destination_table (1..64) -> TABLE_01 .. TABLE_64
مثال: 4 -> TABLE_04
```

ساخت و اعتبارسنجی فقط از طریق `build_move_command()` و `validate_command()` انجام میشود.

---

## ۸. چرخه ارسال و وضعیت

```
PENDING/QUEUED/ASSIGNED
   -> [dispatch API] -> QUEUED (SENDING) -> transport -> ASSIGNED (SENT)
   -> [robot ack: received] -> ASSIGNED
   -> [robot status: in_progress] -> IN_PROGRESS
   -> [robot status: completed] -> COMPLETED
   -> [error/reject/timeout] -> FAILED
```

وضعیتهای پروژه (منبع: `restaurant/mission_statuses.py`):
`pending, queued, assigned, in_progress, completed, failed, cancelled`.

تفکیک مهم: موفقیت ارسال (`SENT`) با تحویل واقعی (`COMPLETED`) متفاوت است.
APIproduct منتظر اتمام حرکت فیزیکی نمیماند.

---

## ۹. Timeout و Retry

- مقدار Timeout: `ROBOT_ACK_TIMEOUT_SECONDS` (پیشفرض ۵ ثانیه).
- هنگام Timeout مأموریت به `FAILED` با `error_type=timeout` میرود، ولی **ارسال مجدد خودکار نمیشود** چون نمیتوان فرض کرد ربات فرمان را دریافت نکرده است.
-Idempotency: هر مأموریت حداکثر یکبار ارسال میشود (`InMemoryOutcomeStore`کلیدشده با `mission_uid`). درخواست مجدد همان نتیجهٔ قبلی را برمیگرداند (`idempotent_replay=true`).
- پاسخ تکراری (duplicate response) در Consumer ربات شناسایی و رد میشود.

---

## ۱۰. احراز هویت ربات (WebSocket)

| مورد | مقدار |
|---|---|
| آدرس | `ws://<host>/ws/robots/<robot_id>/?token=<ROBOT_API_TOKEN>` |
| توکن | `ROBOT_API_TOKEN` از تنظیمات (متغیر محیطی در production) |
| محدوده | هر ربات فقط در گروه `robot_<robot_id>` عضو میشود و فقط فرمانهای مربوط به خودش را میگیرد |

پیامهای ورودی ربات: `ack`, `status`, `reject`, `completed`، `ping`.
پیام خروجی سرور: `command`, `connected`, `ack_received`, `error`, `pong`.

در صورت تنظیم نشدن توکن، اتصال ربات رد میشود (Fail-closed).

---

## ۱۱. لایه ارتباطی (Transport)

| تنظیم | مقدار | توضیح |
|---|---|---|
| `ROBOT_TRANSPORT_BACKEND` | `memory` (پیشفرض) | شبیهساز — مناسب تست و توسعه |
| `ROBOT_TRANSPORT_BACKEND` | `channels` | پخش فرمان از طریق Channel Layer به WebSocket ربات |

برای تست، پیادهسازی سفارشی با `set_robot_transport()` تزریق میشود.

---

## ۱۲. فایلهای ایجاد/تغییریافته

| فایل | تغییر |
|---|---|
| `restaurant/movement_command.py` | جدید — قرارداد استاندارد و نگاشت مقصد |
| `restaurant/robot_transport.py` | جدید — لایه ارتباطی + Idempotency + Outcome |
| `restaurant/mission_dispatch.py` | جدید — سرویس ارسال مأموریت |
| `restaurant/robot_consumer.py` | جدید — WebSocket ربات و پردازش ack |
| `restaurant/events.py` | اضافه شدن `broadcast_robot_command` |
| `restaurant/routing.py` | اضافه شدن مسیر `ws/robots/<robot_id>/` |
| `restaurant/views.py` | اضافه شدن `dispatch_mission_api` |
| `restaurant/urls.py` | اضافه شدن `api/missions/dispatch/` |
| `RestaurantRobot/settings.py` | تنظیمات ROBOT_* |
| `restaurant/tests_mission_dispatch.py` | ۲۰ تست (۱۶ سناریوی الزامی + ۴ تکمیلی) |

---

## ۱۳. تستها

اجرا:

```bash
python manage.py test restaurant.tests_mission_dispatch -v 2
```

نتیجه: **۲۰ تست، هم PASS**.

---

## ۱۴. سازگاری و اقدامات باقیمانده

- **ESP32/STM32**: قرارداد JSON ساده و مستقل از پلتفرم است؛ هر کنترلری که بتواند WebSocket/HTTP را مدیریت کند میتواند فرمانها را مصرف کند.
- **برای اتصال سختافزار واقعی باقی مانده است**:
  1. تنظیم `ROBOT_TRANSPORT_BACKEND=channels` و `ROBOT_API_TOKEN` در محیط production.
  2. تنظیم `CHANNEL_LAYERS` روی Redis (چند پردازش) و sticky sessions.
  3. پیادهسازی سمت فریمور (توسط تیم Embedded) برای اتصال به `ws/robots/<id>/` و ارسال ack.
  4. اتصال الگوریتم مسیریابی/کنترل موتور (خارج از محدودهٔ این مأموریت).
  5. تست End-to-End با ربات واقعی و سناریوهای اضطراری.

جزئیات استقرار (تنظیمات محیطی، Daphne، Redis، بکاپ و چک لیست اتصال) در `DEPLOYMENT.md` است.
