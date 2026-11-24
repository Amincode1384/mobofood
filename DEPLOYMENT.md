# EnjiNex — استقرار و اتصال واقعی ربات (بخش DevOps)

مکمل `MISSION_API_DOCS.md` (بخش ۱۴). هدف: اجرای Daphne + Redis + لایه ارتباطی ربات، بدون شبیهساز.

---

## ۱. متغیرهای محیطی

`cp .env.example .env` سپس مقدارهای واقعی را پر کنید. حداقل برای production:

| متغیر | مقدار production | دلیل |
|---|---|---|
| `DJANGO_SECRET_KEY` | کلید تصادفی بلند | بدون آن با `DEBUG=0` برنامه بالا نمیآید (fail-fast) |
| `DEBUG` | `0` | جلوگیری از صفحات خطا و کلید توسعه |
| `ALLOWED_HOSTS` | لیست صریح | `"*"` با `DEBUG=0` رد میشود |
| `CHANNEL_LAYER_BACKEND` | `redis` | گروههای WebSocket بین Daphne و watcher مشترک شوند |
| `REDIS_URL` | `redis://<host>:6379/0` | آدرس Redis |
| `ROBOT_TRANSPORT_BACKEND` | `channels` | ارسال فرمان از طریق WebSocket به جای شبیهساز |
| `ROBOT_API_TOKEN` | راز تصادفی مشترک با ربات | بدون آن اتصال ربات رد میشود (fail-closed) |
| `SECURE_SSL_REDIRECT` / `SECURE_HSTS_SECONDS` | `1` / `31536000` پشت پروکسی TLS |فقط بعد از پایانه شدن HTTPS تنظیم شود |

تولید راز: `python -c "import secrets; print(secrets.token_urlsafe(50))"`

---

## ۲. اجرای بومی (بدون Docker)

```bash
pip install -r requirements.txt
python manage.py migrate --noinput
python manage.py collectstatic --noinput

# ترمینال ۱ — سرور ASGI/WebSocket
daphne -b 0.0.0.0 -p 8000 RestaurantRobot.asgi:application

# ترمینال ۲ — پردازش رویدادهای زمانمحور (اجباری تحت Daphne)
python manage.py run_timer_watcher
```

نکتهٔ بحرانی: `restaurant/apps.py` watcher را فقط تحت `manage.py runserver` آغاز میکند. تحت Daphne این thread شروع نمیشود؛ بدون فرمان دوم، سفارشها از `cooking` / `sent` به `awaiting_confirmation` نمیروند.

---

## ۳. اجرای کانتینر (اختیاری)

```bash
cp .env.example .env        # رازها را پر کنید
mkdir -p data               # محل db.sqlite3 قابل بکاپ
docker compose up -d --build
docker compose logs -f web timer-watcher
```

`Dockerfile` و `docker-compose.yml` فراهم شدند اما در این محیط Docker نصب نبود، پس ساخت image تأییدنشده است. تصویر از Python 3.13 استفاده میکند (محیط توسعه محلی 3.15 RC بود).

---

## ۴. مقیاس و Sticky Sessions

- پیکربندگی فعلی: **یک پردازش Daphne** — نیازی به sticky session نیست، و SQLite هم فقط یک نویسنده معتبر میداند.
- اگر چند worker اضافه شد: Redis الزامی است و لودبالانسر باید همان اتصال WebSocket را همیشه به همان worker بفرستد (`ip_hash` در nginx یا cookie-based affinity). در غیر این صورت پیام ack به worker دیگری میرسد و outcome Store (که در حافظهٔ هر worker است) آن را نمیشناسد.
- `InMemoryOutcomeStore` و `_SHARED_TRANSPORT` در حافظهٔ پردازشاند. برای multi-process باید به یک store مشترک (Redis/DB) مهاجرت کنیم. تا آن زمان تعداد workerها = ۱ نگه دارید.

---

## ۵. تأیید سلامت قبل از تست واقعی

```bash
# تنظیمات production بدون بالا آوردن سرور
DEBUG=0 DJANGO_SECRET_KEY=test ALLOWED_HOSTS=localhost \
  ROBOT_TRANSPORT_BACKEND=channels ROBOT_API_TOKEN=test-token \
  CHANNEL_LAYER_BACKEND=redis python manage.py check --deploy

# اتصال WebSocket ربات (جایگزیدن wscat)
npx wscat -c "ws://localhost:8000/ws/robots/RBOT-01/?token=<ROBOT_API_TOKEN>"
# انتظار: {"event":"connected","robot_id":"RBOT-01"}

# ارسال ack نمونه از سمت ربات
# {"event":"ack","command_id":"<uuid>","mission_id":"<mission_uid>","status":"accepted"}
```

چک لیست اتصال:
1. `channels` فعال است و توکن تنظیم شده (بدون توکن اتصال رد میشود).
2. Redis بالا است و `CHANNEL_LAYER_BACKEND=redis`؛ فرمان در گروه `robot_<id>` پخش میشود.
3. فریمور ربات (تیم Embedded) به آدرس بالا متصل و ack میفرستد.
4. وضعیت مأموریت در دیتابیس به ترتیب `assigned → in_progress → completed` تغییر میکند.
5. تست E2E با ربات واقعی و سناریوهای اضطراری (قطع ارتباط، timeout، رد فرمان).

---

## ۶. بکاپ و بازیابی (دستی و اختیاری)

تصمیم کاربر: **بکاپ در این مرحله لازم نیست**. بنابراین هیچ زمان‌بندی، cron،
routine یا job خودکاری وجود ندارد و بکاپ در مسیر بحرانی استقرار قرار نگرفته است.
فرمان زیر فقط زمانی اجرا می‌شود که خودتان صدا بزنید:

```bash
# بکاپ کامل خصوصی (پیش‌فرض: backups/backup_<تاریخ>_<زمان>.json)
python manage.py backup_db

# مسیر دلخواه
python manage.py backup_db --output /path/to/backup.json

# بکاپ ناشناس‌‌شده برای اشتراک‌گذاری (تلفن ماسک، نام/توکن‌ها پاک)
# توجه: این نسخه برای Restore کامل مناسب نیست.
python manage.py backup_db --anonymize
```

ویژگی‌های فرمان:

- از ابزار استاندارد `dumpdata` استفاده می‌کند؛ وابستگی جدید نصب نمی‌شود.
- خروجی به یک فایل موقت در همان پوشه نوشته، اعتبارسنجی، و سپس با
  `os.replace` به‌صورت **اتمی** جابه‌جا می‌شود؛ در صورت خطا هیچ فایل ناقصی
  باقی نمی‌ماند و پیام خطای فارسی داده می‌شود.
- فایل دیتابیس نبودن → خطا قبل از نوشتن؛ غیبت فایل بکاپ ساخته نمی‌شود.
- فقط SQLite پشتیبانی می‌شود (مطابق تنظیمات فعلی، `DB_NAME` با پیش‌فرض
  `BASE_DIR/db.sqlite3`).
- `sessions` و `admin` (شامل لاگ‌های ادمین) عمداً از بکاپ حذف می‌شوند.
- خروجی در `.gitignore` قرار دارد (`backups/` و `backup_*.json`) و هرگز commit نمی‌شود.

بازیابی:

```bash
# ۱) هر دو پردازش را متوقف کنید (نوشتن همزمان به SQLite ممنوع)
python manage.py loaddata backups/backup_2026-11-22_101500.json
python manage.py migrate --noinput   # در صورت نیاز به ساختار جدید
```

نکته: ردیف‌های سفارش شامل نام و تلفن مشتری (PII) هستند. فایل بکاپ را خصوصی
نگه دارید، در Git یا پیام‌رسان‌ها قرار ندهید و برای اشتراک از `--anonymize`
استفاده کنید.

Redis (AOF/RDB در حجم `redis_data`)، `media/` و `staticfiles/` را در صورت نیاز
جداگانه بکاپ بگیرید.

---

## ۷. وضعیت آمادگی

- **آماده**: تنظیمات fail-fast، ASGI/Daphne، مسیر WebSocket ربات، لایه Redis، فرمان watcher، الگوی محیطی.
- **تأییدنشده**: ساخت Docker image (Docker در محیط فعلی موجود نیست)، اتصال به ربات/فریمور واقعی، تست E2E.
- **محدودیت معماری**: تکپردازش بودن به دلیل SQLite و outcome store حافظهای؛ مقیاس_multi-worker نیاز به مهاجرت به store مشترک دارد.
