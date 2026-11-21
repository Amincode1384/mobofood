# گزارش punchline امنیتی — پنل آشپزخانه EnjiNex

**پروژه:** Robotic-Restaurant-main (Django + Channels)
**تاریخ:** ۱۴۰۵/۰۸/۳۰
**مأموریت:** ایمن‌سازی دسترسی به پنل آشپزخانه و محدودسازی آن به کاربران مجاز
**استATUS:** انجام‌شده — هیچ تغییر کدی لازم نبود

---

## ۱. وضعیت امنیتی پنل قبل از تغییرات

پنل آشپزخانه قبل از این punchline تحت محافظت زیر قرار داشت:

| لایه | مکانیزم | وضعیت |
|---|---|---|
| احراز هویت | Django `AuthenticationMiddleware` + Session | فعال |
| مجوز داشبورد | `@user_passes_test(is_staff, login_url="/admin/login/")` روی `dashboard()` | فعال |
| مجوز APIهای تغییر وضعیت سفارش | `_kitchen_api_required` (is_staff + authenticated + fail-closed) | فعال |
| مجوز WebSocket آشپزخانه | `KitchenConsumer.connect()` چک می‌کند `is_authenticated and is_staff` | فعال |
| CSRF | `CsrfViewMiddleware` فعال؛ Session-based endpoints CSRF را الزامی می‌کنند | فعال |
| Rate limiting | `order_security.rate_limited` روی پیگیری سفارش و تأیید مشتری | فعال |
| توکن پیگیری مشتری | فقط از Header/Session مجاز است؛ هرگز credential آشپزخانه نیست | فعال |

---

## ۲. آسیب‌پذیری‌ها و مشکلات شناسایی‌شده

حلقه امنیتی península کامل است و هیچ gouge Critical یا High پیدا نشد. نقاط قوة:

- **هیچ endpoint تغییر وضعیتی بدون محافظ باقی نمانده است.** `UrlCoverageTests` همه شش endpoint (`accept`, `reject`, `ready`, `send`, `timer`, `resolve`) را برای کاربر ناشناس 403 می-être.
- **توکن پیگیری مشتری نمی‌تواند مجوز آشپزخانه بگذارند** (`customer_token_not_allowed`، 403).
- **Transitionهای غیرمجاز توسط `order_statuses.ALLOWED_TRANSITIONS` مسدود می‌شوند** حتی برای کارکنان.
- **داشبورد برای کاربران غیرکار্যকر marito Redirect می‌شود** به `/admin/login/`.
- **WebSocket کانال آشپزخانه برای کاربران غیراستاف بسته می‌شود** (کد 4403).

---

## ۳. معماری کنترل دسترسی انتخاب‌شده

اقدام انجام‌نشده، چرا که معماری فعلی کافی است:

- **لاگین + is_staff** برای تمام عملیات آشپزخانه (صفحه داشبورد، APIهای وضعیت سفارش، WebSocket، ماموریت ربات).
- **توکن پیگیری سفارش (Order Tracking Token)** فقط برای عملیات مشتری (مشاهده سفارش، تأیید تحویل).
- **Fail-closed:** هر خطای غیرمنتظره در بررسی هویت ⇒ 403.
- **هرگونه تغییر وضعیت توسط Staff فقط از طریق POST** (GET ⇒ 405).
- **CSRF الزام دارد** برای Session-based درخواست‌های تغییردهنده.

---

## ۴. روش احراز هویت و مجوزدهی پیاده‌سازی‌شده

| سطح | متد | توضیح |
|---|---|---|
| Page (داشبورد) | `@user_passes_test(is_staff)` | Redirect به `/admin/login/` برای غیراستاف |
| API سفارش | `_kitchen_api_required` | `is_authenticated and is_staff`؛ übrigen 403 |
| API پیگیری | `authorize_order_request()` | توکن پیگیری در Header/Session |
| WebSocket آشپزخانه | `KitchenConsumer.connect()` | `is_authenticated and is_staff` |
| Mission API | `_kitchen_api_required` | همان لایه مجوز آشپزخانه |

---

## ۵. فایل‌های ایجادشده یا اصلاح‌شده

هیچ فایلی ایجاد یا اصلاح نشد، چون راهکار فعلی از نظر امنیتی مناسب است (ببخشید دستور missão: «اگر راهکار فعلی پروژه از نظر امنیتی مناسب است، صرفاً برای ایجاد تغییرات بیشتر آن را بازنویسی نکن»).

---

## ۶. تغییرات پایگاه داده و Migrationها

هیچ تغییر یا Migration جدیدی لازم نبود.

---

## ۷. Endpointها و مسیرهای محافظت‌شده

### محافظت شده توسط `_kitchen_api_required` (فقط Staff):

| Endpoint | متد | fungi |
|---|---|---|
| `/api/orders/` | GET | لیست سفارش‌های آشپزخانه |
| `/api/orders/<id>/accept/` | POST | تایید سفارش (new → cooking) |
| `/api/orders/<id>/reject/` | POST | رد سفارش |
| `/api/orders/<id>/ready/` | POST | علامت آماده (cooking → ready) |
| `/api/orders/<id>/send/` | POST | ارسال به ربات (ready → sent) |
| `/api/orders/<id>/timer/` | POST | تنظیم تایمر آماده‌سازی |
| `/api/orders/<id>/resolve/` | POST | رسیدگی به مشکل مشتری |
| `/api/missions/dispatch/` | POST | دسپچ مأموریت ربات |
| `/api/missions/` | GET | لیست مأموریت‌ها |
| `/api/missions/<uid>/` | GET | جزئیات مأموریت |
| `/api/missions/<uid>/status/` | POST | بروزرسانی وضعیت مأموریت |
| `/api/missions/<uid>/cancel/` | POST | لغو مأموریت |

### محافظت شده توسط `@user_passes_test(is_staff)`:

| Path | متد |
|---|---|
| `/dashboard/` | GET |

### محافظت شده توسط `authorize_order_request` (توکن پیگیری مشتری):

| Endpoint | متد |
|---|---|
| `/api/orders/<id>/` | GET (پیگیری سفارش) |
| `/api/orders/<id>/confirm/` | POST (تأیید تحویل مشتری) |
| `/order/<id>/` | GET (صفحه پیگیری HTML) |

### WebSocket:

| Path | حفاظت |
|---|---|
| `ws/kitchen/` | `is_authenticated and is_staff` |

### عمومی (بدون احراز هویت):

- `/` (صفحه مشتری، ثبت سفارش)
- `/api/create_order/` (ثبت سفارش جدید)
- `/api/foods/` و `/api/categories/` (خواندن منو)
- `/qr/...` (تصاویر QR)

---

## ۸. تست‌های طراحی شده و نتایج اجرای آن‌ها

تست‌های امنیتی موجود در پروژه (بدون اضافه کردن تست جدید، چرا که پوشش کافی است):

| فایل | تعداد تست | نتیجه |
|---|---|---|
| `restaurant/tests_order_security.py` | ۳۱ | PASS |
| `restaurant/tests_order_security_api.py` | ۳۴ | PASS |
| **كلّي** | **۶۵** | **همه PASS، خروجی صفر** |

 سناریوهای پوشش‌یافته:

- ناشناس نمی‌تواند وضعیت سفارش را عوض کند (Accept/Reject/Ready/Send/Timer/Resolve)
- کاربر عادی (مشتری) نمی‌تواند به لیست آشپزخانه یا داشبورد دسترسی پیدا کند
- Staff غیرفعال (is_active=False) رد می‌شود
- CSRF الزامی است برای Session-based درخواست‌ها
- Transitionهای غیرمجاز (مثلاً Skip به Robot) مسدود می‌شوند
- توکن پیگیری مشتری نمی‌تواند credential آشپزخانه باشد
- شناسه نامعتبر سفارش ⇒ 404
- Payload دست‌کاری شده ن induce تغییر در فیلدهای سفارش
- کارکنان می‌توانند زنجیره کامل وضعیت را طی کنند
- capabilities مشتری (ثبت سفارش، پیگیری، تأیید تحویل) حفظ می‌شوند

اجرای کامل:

```
python manage.py test restaurant.tests_order_security restaurant.tests_order_security_api -v 1
Ran 65 tests in 106.492s
OK
```

---

## ۹. مشکلات باقی‌مانده و محدودیت‌ها

- **DEBUG=1 به طور پیش‌فرض** در محیط توسعه؛ در production باید `DEBUG=0` و `DJANGO_SECRET_KEY` از متغیر محیطی تنظیم شود.
- **ALLOWED_HOSTS='*'** به صورت پیش‌فرض؛ در production باید لیست صریح hosts تنظیم شود.
- **روابطocket ربات (`ws/robots/<id>/`)** نیاز به `ROBOT_API_TOKEN` و `ROBOT_TRANSPORT_BACKEND=channels` دارد که در production تنظیم نشده است (fail-closed، مقصود).
- **Channel layer InMemory** فقط یک پردازش را підтримک می‌کند؛ برای multi-worker نیاز به Redis است.
- **ساخت تصویر Docker** در این محیط تایید نشده است (Docker نصب نیست).

---

## ۱۰. پیشنهادهای برای افزایش امنیت در آینده

1. در production `DEBUG=0` و `DJANGO_SECRET_KEY` از متغیر محیطی ست شود.
2. `ALLOWED_HOSTS` به لیست صریح میزبانها تغییر یابد.
3. `SESSION_COOKIE_SECURE` و `CSRF_COOKIE_SECURE` در پشت TLS فعال شوند (فعلاً زیر conditioning HTTPS است).
4. برای Comulti-worker، Channel layer به Redis مهاجرت و sticky session تنظیم شود.
5. Toکن `ROBOT_API_TOKEN` در production تنظیم شود تا ربات_real می‌تواند وصل شود.
6. tekintve مراجعه دوره‌ای به URLهای جدید برای اطمینان ازDecorater `_kitchen_api_required`.

---

## معیارهای پذیرش — تأیید شده

- [x] کاربران ناشاناس نمی‌توانند به پنل آشپزخانه دسترسی پیدا کنند (302 Redirect)
- [x] کاربران احراز هویت‌شده فاقد مجاز نمی‌توانند وارد پنل شوند (403)
- [x] کاربران مجاز می‌توانند بدون اختلال از پنل استفاده کنند (تست‌های StaffAccessTests)
- [x] تمام APIها و Endpointهای حساس محافظت شده‌اند (UrlCoverageTests)
- [x] دسترسی مستقیم و دورزدن رابط کاربری امکان‌پذیر نیست (Fail-closed + 403)
- [x] عملیات غیرمجاز هیچ تغییری در سفارش‌ها ایجاد نمی‌کند (StatusUNCHANGED در تستها)
- [x] عملیات غیرمجاز باعث ایجاد مأموریت ربات نمی‌شود (403 قبل از هر دسپچ)
- [x] نشست‌های نامعتبر و منقضی‌شده به درستی مدیریت می‌شوند (test_inactive_staff_is_rejected)
- [x] تست‌های امنیتی و رگرسیون اجرا شده‌اند (۶۵ تست، همه PASS)
- [x] عملکرد بخش‌های غیرمرتبط حفظ شده است (CustomerFlowsStillWorkTests)

---

**نتیجه نهایی:** پنل آشپزخانه EnjiNex از نظر کنترل دسترسی امن است؛ هیچ تغییر کدی لازم نیست. reports آماده است.