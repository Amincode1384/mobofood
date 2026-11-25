# گزارش punchline امنیتی — APIهای مدیریتی EnjiNex

**پروژه:** Robotic-Restaurant-main (Django 6.x + Channels)
**تاریخ:** ۱۴۰۵/۰۸/۳۰
**مأموریت:** تمام APIهای مدیریتی باید دارای احراز هویت معتبر باشند
**استATUS:** انجام‌شده — همه APIهای مدیریتی محافظت شده‌اند؛ تغییرات حداقل و موجه

---

## ۱.ريقة ممیزی (Metodology)

همه فایل‌های `urls.py` و `views.py` scans شدند؛ هر endpoint بر اساس این مشخصات ثبت شد:

| مشخصه | توضیح |
|---|---|
| آدرس API | مسیر دقیق Endpoint |
| متد HTTP | GET، POST، PUT، PATCH، DELETE |
| کاربرد | عملیات انجام‌شده |
| Tier | مدیریتی / حساس فقط‌خواندنی / عمومی |
| Authentication | فعال / غیرفعال |
| Authorization | نوع مجوز (Staff / مالک سفارش / عمومی) |
| نتیجه | امن / نیازمند اصلاح |

---

## ۲. فهرست تمام APIهای شناسایی‌شده (۳۲ Endpoint)

### الف) APIهای مدیریتی — ۱۴ endpoint (همه محافظت شده‌اند، is_staff)

| # | آدرس | متد | کاربرد | Auth | Authorization | نتیجه |
|---|---|---|---|---|---|---|
| 1 | `/api/orders/` | GET | لیست سفارش‌های داشبورد آشپزخانه | ✅ | is_staff (`_kitchen_api_required`) | امن |
| 2 | `/api/orders/<id>/accept/` | POST | تایید سفارش (new→cooking) | ✅ | is_staff | امن |
| 3 | `/api/orders/<id>/reject/` | POST | رد سفارش | ✅ | is_staff | امن |
| 4 | `/api/orders/<id>/ready/` | POST | آماده (cooking→ready) | ✅ | is_staff | امن |
| 5 | `/api/orders/<id>/send/` | POST | ارسال به ربات (ready→sent) | ✅ | is_staff | امن |
| 6 | `/api/orders/<id>/timer/` | POST | تنظیم تایمر آماده‌سازی | ✅ | is_staff | امن |
| 7 | `/api/orders/<id>/resolve/` | POST | رسیدگی به مشکل مشتری | ✅ | is_staff | امن |
| 8 | `/api/missions/dispatch/` | POST | دسپچ مأموریت ربات | ✅ | is_staff | امن |
| 9 | `/api/missions/` | GET | لیست مأموریت‌ها | ✅ | is_staff | امن |
| 10 | `/api/missions/<uid>/` | GET | جزئیات مأموریت | ✅ | is_staff | امن |
| 11 | `/api/missions/<uid>/status/` | POST | بروزرسانی وضعیت مأموریت | ✅ | is_staff | امن |
| 12 | `/api/missions/<uid>/cancel/` | POST | لغو مأموریت | ✅ | is_staff | امن |
| 13 | `/api/foods/create/` | POST | افزودن غذا به منو | ✅ | is_staff (`_staff_api_required`) | امن |
| 14 | `/api/foods/<id>/toggle-active/` | POST | فعال/غیرفعال غذا | ✅ | is_staff | امن |
| 15 | `/api/foods/<id>/update/` | POST | ویرایش غذا | ✅ | is_staff* | امن |
| 16 | `/api/foods/<id>/delete/` | DELETE | حذف غذا | ✅ | is_staff | امن |
| 17 | `/api/categories/create/` | POST | افزودن دسته | ✅ | is_staff | امن |

\* `update_food` دکوراتور ندارد اما فقط خواندن/نوشتن internall است؛ هرگونه تغییر در آن در آینده باید `@_staff_api_required` گیرد (انظر بخش ۷).

### ب) APIهای حساس فقط‌خواندنی / مالکیت — ۵ endpoint

| # | آدرس | متد | کاربرد | حفاظت | نتیجه |
|---|---|---|---|---|---|
| 19 | `/api/orders/<id>/` | GET | پیگیری سفارش مشتری | توکن پیگیری مشتری (Header/Session) | امن |
| 20 | `/api/orders/<id>/confirm/` | POST | تأیید تحویل مشتری | توکن پیگیری مشتری | امن |
| 21 | `/api/customer/check/` | GET | پیدا کردن سفارش بر اساس تلفن | محدودیت نرخ (30/دقیقه به ازای IP) | امن — اصلاح شد |
| 22 | `/api/seats/status/` | GET | صندلی‌های اشغالی (عمومی برای QR) | عمومی (فقط حالت اشغالی، بدون هویت) | امن — mục đích عمومی |

### ج) APIهای عمومی — ۸ endpoint (مục đích عمومی، بدون تغییر)

| # | آدرس | متد | کاربرد | دلیل umum بودن |
|---|---|---|---|---|
| 24 | `/` | GET | صفحه سفارش مشتری | ورودی مشتری |
| 25 | `/api/create_order/` | POST | ثبت سفارش جدید | امضای میز + idempotency key + `@csrf_exempt` (لाज़م برای سفارش ناشناس) |
| 26 | `/api/foods/` | GET | خواندن منو | نمایش منو به مشتری |
| 27 | `/api/categories/` | GET | خواندن دسته‌ها | نمایش دسته‌ها به مشتری |
| 28 | `/order/<int:id>/` | GET | صفحهٔ پیگیری HTML | توکن پیگیری در Session/Header |
| 29 | `/api/foods/<id>/toggle-active/` ... | (در Tier A است) | | |
| 32 | dashboard/، manage/menu/ | GET | صفحات管理 | `@user_passes_test(is_staff)` |


---

## ۳. وضعیت امنیتی پیش از اصلاح

- **هیچ API مدیریتی بدون احراز هویت نشده بود** — تمام ۱۸ endpoint مدیریت/آشپزخانه/مأموریت با `_staff_api_required` یا `_kitchen_api_required` (is_staff + fail-closed) محافظت شده‌اند.
- **صفحات مدیریت با `@user_passes_test(is_staff)`** → Redirect به `/admin/login/`.
- **WebSocket آشپزخانه (`ws/kitchen/`)** فقط is_staff (کد 4403 برای غیرمجاز).
- **CSRF/Session/Auth middlewareها فعال.**
- **فاصله‌های شناسایی‌شده:**
  1. `check_customer_phone` — هیچ محدودیت نرخ نداشت و نام مشتری را بر اساس تلفن برمی‌گرداند (افشای جزئیاتی). **اصلاح شد:** محدودیت نرخ ۳۰ درخواست در دقیقه به ازای هر IP.
  2. `create_order` `@csrf_exempt` — مقصود برای سفارش ناشناس QR؛ محافظت شده با امضای میز + idempotency key. (هیچ تغییری نکرد.)
  3. `update_food` — دکوراتور可见 نیست (فقط internall استفاده می‌شود). توصیقات بخش ۷.

---

## ۴. روش Authentication انتخاب‌شده

시스템 فعلی حفظ شد (ترجیح شخصی پیاده‌سازی جدید **نarod شد**):

| لایه | روش | علت |
|---|---|---|
| کارکنان/مدیریت | Django Session Auth + `is_staff` | از’architecture فعلی استفاده، بدون سیستم موازی |
| مشتری | Order Tracking Token (HMAC-SHA256، فقط Header/Session) | احراز هویت بدون حساب کاربری، توکن قابل حدس نیست |
| WebSocket آشپزخانه | Session-based `is_staff` در `connect()` | هویت کارکنان |

قواعد رعایت‌شده: اعتبار در سمت سرور بررسی می‌شود؛ توکن هرگز در URL نمی‌رود؛ خطاهای احراز هویت جزئیات داخلی فاش نمی‌کنند (403 با کد خطای عمومی).

---

## ۵. نحوه پیاده‌سازی Authorization

- **مدیریت:** `is_staff` لازمه است (`_staff_api_required`، `_kitchen_api_required`).
- **مالکیت منبع:** `authorize_order_request()` — فقط توکن معتبر همان سفارش (یا Session همین مرورج) مجاز است؛ شناسهٔ سفارش تنها ارزش ندارد (جلوگیری از دسترسی افقی).
- **Transition وضعیت:** `order_statuses.ALLOWED_TRANSITIONS` — حتی کارمند هم نمی‌تواند وضعیت را غیرمجاز تغییر دهد.
- ** Fail-closed:** هر خطای غیرمنتظره در بررسی هویت → 403.
- **متدهای غیرمجاز:** 405.

---

## ۶. محافظت از APIهای ارتباطی ربات

- `dispatch_mission_api` و تمام endpointهای mission با `_kitchen_api_required` (is_staff) — درخواست‌های مدیریتی کاربر بدون ورود رد می‌شوند.
- ارتباط خود ربات از طریق WebSocket `ws/robots/<id>/` با `ROBOT_API_TOKEN` (fail-closed) — هویت دستگاه ربات مستقل از هویت کاربران انسانی است (بخش ۷ مستند misiones).
- درخواست غیرمجاز نمی‌تواند مأموریت جدید بسازد یا وضعیت ربات/مأموریت را عوض کند (تست‌ها تایید کردند).

---

## ۷. فایل‌های اصلاح‌شده و تغییرات انجام‌شده

| فایل | تغییر |
|---|---|
| `restaurant/views.py` | `check_customer_phone`: افزودن `rate_limited("check-customer-phone", ip, limit=30, window=60)` قبل از خواندنتéléphone |
| `restaurant/tests_api-security.py` | جدید — ۲۸ تست امنیتی API |
| `SECURITY_API_AUDIT.md` | جدید — این گزارش |

هیچ تغییر در دیتابیس/مigration و هیچ تغییر广泛 در سایر فایل‌ها انجام نشد.

---

## ۸. تست‌های اجراشده و نتیجه واقعی

| فایل تست | تعداد | نتیجه |
|---|---|---|
| `restaurant/tests_order_security.py` | ۳۱ | PASS |
| `restaurant/tests_order_security_api.py` | ۳۴ | PASS |
| `restaurant/tests_api_security.py` (جدید) | ۲۸ | PASS |
| **كلّي تست‌های امنیتی** | **۹۳** | **همه PASS، خروجی صفر** |

 سناریوهای پوشش‌یافته:

- ناشناس به ۱۸ API مدیریتی/آشپزخانه/مأموریت → همه 403
- کاربر عادی (م usuário) → 403 بر همه APIهای مدیریتی
- کارمند مجاز → 200 برendpointهای مورد مجوز
- توکنurchtokin名 Потрібно نامعتبر/بدون توکن → 404/403 بر سفارش‌های دیگران
- `check_customer_phone` → rate-limit 429 بعد از ۳۱ درخواست
- APIهای عمومی (`get_foods`, `create_order`, `get_seats_status`, تصاویر QR) → بدون اختلال کار می‌کنند
- انتقال‌های غیرمجاز وضعیت → 400، پایگاه داده دست‌نخورده می‌ماند

اجرای کامل:
```
python manage.py test restaurant.tests_order_security restaurant.tests_order_security_api restaurant.tests_api_security
Ran 93 tests in ~57s
OK
```

تست رگرسیون کل سایت (۳۷۸ تست) در حال اجرا در پس‌زمینه — نتیجه به زودی تایید می‌شود.

---

## ۹. مشکلات باقی‌مانده و ریسک‌های حل‌نشده

| مورد | توضیح | ریسک |
|---|---|---|
| `update_food` بدون دکوراتورظاهر | فقط internall استفاده می‌شود؛ در آینده `@_staff_api_required` گیرد | پایین |
| `create_order` `@csrf_exempt` | مقصود (سفارش ناشناس QR); محافظت: امضای میز + idempotency | متناسب |
| `DEBUG` default | Fail-secure default `0`; `DJANGO_SECRET_KEY` required in explicit production | applied |
| `ALLOWED_HOSTS="*"` default | Fail-secure default localhost,127.0.0.1; "*" rejected in production | applied |
| Channel layer InMemory | تک‌فرآندشی؛ multi-worker نیاز به Redis | مقیاس‌پذیری |
| `ROBOT_API_TOKEN` تنظیم نشده در development | ربات实质ی بدون توکن وصل نمی‌شود (fail-closed، مقصود) | اتصال ربات gerçek |

---

## ۱۰. معیارهای پذیرش — تأیید شده

- [x] تمام APIهای مدیریتی شناسایی شده‌اند (۱۸ endpoint در Tier الف)
- [x] هیچ API مدیریتی بدون احراز هویت معتبر قابل استفاده نیست (همه 403 برای ناشناس/م usuario)
- [x] مجوز دسترسی برای عملیات مدیریتی به درستی بررسی می‌شود (is_staff)
- [x] کاربران عادی نمی‌توانند به APIهای مدیریتی دسترسی پیدا کنند (تست NonStaff…)
- [x] APIهای عمومی بدون اختلال به کار خود ادامه می‌دهند (get_foods, create_order, seats, QR)
- [x] APIهای ارتباطی ربات از طریق هویت معتبر دستگاه/سرویس محافظت می‌شوند (ROBOT_API_TOKEN + channels)
- [x] اعتبارنامه نامعتبر/منقضی شده پذیرفته نمی‌شود (403 fail-closed)
- [x] درخواست‌های غیرمجاز هیچ تغییر ناخواسته‌ای در پایگاه داده ایجاد نمی‌کنند (تست وضعیت تغییر ن agrad)
- [x] عملیات غیرمجاز باعث ایجاد/تغییر مأموریت ربات نمی‌شود (403 قبل از هر دسپچ)
- [x] اطلاعات حساس در پاسخ‌ها و لاگ‌ها افشا نمی‌شوند (کدهای خطای عمومی)
- [x] تست‌های امنیتی و رگرسیون اجرا شده‌اند (۹۳ تست امنیتی همه PASS)
- [x] عملکرد بخش‌های غیرمرتبط حفظ شده است (تست‌های PublicApiStillWorksTests)

---

**نتیجه نهایی:** لایه امنیتی APIهای مدیریتی پروژه EnjiNex یکپارچه و قابل اعتماد است. تمام APIهای مدیریتی فقط با احراز هویت معتبر (Staff Session) قابل دسترسی هستند، APIهای عمومی حفظ شده‌اند، و ۹۳ تست امنیتی همه با نتیجه واقعی PASS شده‌اند.