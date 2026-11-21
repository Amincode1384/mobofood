# راهنمای مبتدی برای پروژه رستوران رباتیک EnjiNex

این راهنما برای کسی نوشته شده است که تا به حال برنامه‌نویسی نکرده است.
هدف: بفهمیم پروژه چگونه کار می‌کند، هر فایل چه نقشی دارد و کدها چطور به هم وصل می‌شوند.

---

## ۱. پروژه چیست؟

یک **وب‌سایت رستوران** است که با **Django** (فریم‌ورک پایتون) ساخته شده.
سه نوع کاربر دارد:

1. **مشتری**: با QR روی میز، منو را می‌بیند و سفارش می‌دهد.
2. **کارکنان آشپزخانه**: سفارش‌ها را در داشبورد می‌بینند و وضعیت را عوض می‌کنند.
3. **مدیر منو**: غذا و دسته‌بندی اضافه/ویرایش/حذف می‌کند.

---

## ۲. ساختار فولدرها (نقشه پروژه)

```
Robotic-Restaurant-main/
├── manage.py                 # دستورات Django (runserver, test, migrate)
├── requirements.txt          # کتابخانه‌های لازم
├── RestaurantRobot/          # تنظیمات کلی پروژه
│   ├── settings.py           # تنظیمات (DEBUG, DB, STATIC, ...)
│   ├── urls.py               # آدرس‌های کلی (به restaurant/urls.py وصل می‌شود)
│   ├── asgi.py / wsgi.py     # نقطه ورود سرور
├── restaurant/               # اپلیکیشن اصلی (همه منطق اینجاست)
│   ├── models.py             # تعریف جدول‌های دیتابیس
│   ├── views.py              # منطق (تابع‌هایی که به درخواست پاسخ می‌دهند)
│   ├── urls.py               # آدرس‌های API و صفحات
│   ├── admin.py              # پنل ادمین Django
│   ├── consumers.py          # WebSocket (real-time)
│   ├── tests*.py             # تست‌ها
│   └── migrations/           # تغییرات دیتابیس
├── templates/                # فایل‌های HTML
│   ├── customer.html
│   ├── Dashboard.html
│   ├── menu_management.html
│   ├── order_tracking.html
│   └── partials/            # بخش‌های قابل استفاده مجدد
├── static/                   # CSS و JS و تصاویر
│   ├── css/
│   └── js/
└── media/                    # تصاویر آپلودشده غذاها
```

---

## ۳. جریان یک درخواست (از مرورگر تا دیتابیس)

```
مرورگر کاربر
   │  (درخواست HTTP: GET /manage/menu/ یا POST /api/foods/create/)
   ▼
RestaurantRobot/urls.py   →  restaurant/urls.py
   │
   ▼
restaurant/views.py  (تابع مربوطه، مثلاً menu_management یا create_food)
   │
   ├──► restaurant/models.py  (خواندن/نوشتن در دیتابیس)
   │
   └──► templates/...html     (رندر صفحه) یا JsonResponse (پاسخ JSON)
   │
   ▼
مرورگر کاربر (نمایش نتیجه)
```

**رابطه کلیدی**: `urls.py` فقط «آدرس → تابع» را تعریف می‌کند. منطق واقعی در `views.py` است. داده‌ها در `models.py` (دیتابیس) ذخیره می‌شوند.

---

## ۴. مدل‌ها (models.py) — «جدول‌های دیتابیس»

هر کلاس = یک جدول در دیتابیس.

```python
class Category(models.Model):
    name = models.CharField(max_length=100)   # نام دسته، مثلاً "پیتزا"
    icon = models.CharField(max_length=50, default="🍽")  # آیکن، مثلاً "🍕"
```

```python
class Food(models.Model):
    category = models.ForeignKey(Category, ...)  # هر غذا به یک دسته وصل است
    name = models.CharField(max_length=100)       # نام غذا
    description = models.TextField(blank=True)    # توضیحات
    price = models.PositiveIntegerField()         # قیمت (تومان)
    stock = models.PositiveIntegerField(default=0) # موجودی (تعداد قابل سفارش)
    is_active = models.BooleanField(default=True)  # فعال/غیرفعال (مدیریتی)
    image = models.ImageField(upload_to="foods/", blank=True, null=True)
```

**نکته مهم**: `stock` (موجودی عددی) با `is_active` (فعال/غیرفعال مدیریتی) دو چیز جدا هستند:
- `stock = 0` یعنی «ناموجود» (موجودی تمام شده).
- `is_active = False` یعنی «خارج از منو» (مدیر تصمیم گرفته نمایش داده نشود).

```python
class Order(models.Model):
    table = models.CharField(max_length=20)       # شماره میز
    total = models.PositiveIntegerField()         # مبلغ کل
    status = models.CharField(...)                # وضعیت: new, accepted, ...
    seats = models.CharField(...)                 # صندلی‌های رزروشده
    customer_name / customer_phone               # اطلاعات مشتری
    tracking_nonce / tracking_token_hash          # توکن پیگیری امن
```

```python
class OrderItem(models.Model):
    order = models.ForeignKey(Order, ...)   # هر آیتم به یک سفارش وصل است
    name = models.CharField(max_length=100)  # نام غذا (کپی، نه ارجاع)
    quantity = models.IntegerField()         # تعداد
    price = models.IntegerField()            # قیمت (کپی، نه ارجاع)
```

**چرا OrderItem نام و قیمت را خودش نگه می‌دارد؟**
تا اگر غذا بعداً حذف یا ویرایش شد، تاریخچه سفارش‌های قبلی دست‌نخورده بماند.

---

## ۵. آدرس‌ها (urls.py)

فایل `restaurant/urls.py` می‌گوید: «این آدرس → این تابع».

```python
# صفحات
path("", views.customer, name="customer"),                    # صفحه مشتری
path("dashboard/", views.dashboard, name="dashboard"),        # داشبورد آشپزخانه
path("manage/menu/", views.menu_management, name="menu_management"),  # مدیریت منو

# غذاها (API)
path("api/foods/", views.get_foods, name="foods"),            # لیست غذاها
path("api/foods/create/", views.create_food, name="create_food"),     # ایجاد غذا
path("api/foods/<int:id>/update/", views.update_food, name="update_food"),  # ویرایش
path("api/foods/<int:id>/delete/", views.delete_food, name="delete_food"),   # حذف
path("api/foods/<int:id>/toggle-active/", views.set_food_active, ...),      # فعال/غیرفعال

# دسته‌ها
path("api/categories/", views.get_categories, name="get_categories"),
path("api/categories/create/", views.create_category, name="create_category"),

# سفارش‌ها
path("api/create_order/", views.create_order, name="create_order"),
path("api/orders/", views.get_orders, name="get_orders"),
...
```

**`<int:id>`** یعنی آدرس شامل یک عدد است (شناسه غذا/سفارش).

---

## ۶. منطق (views.py) — «مغز پروژه»

هر تابع یک درخواست را پردازش می‌کند. نمونه‌ها:

### ۶.۱ صفحه مدیریت منو
```python
@user_passes_test(lambda user: user.is_staff, login_url="/admin/login/")
def menu_management(request):
    categories = Category.objects.prefetch_related("foods").order_by("id")
    return render(request, "menu_management.html", {
        "categories": categories,
        "food_count": Food.objects.count(),
    })
```
- `@user_passes_test(...)`: فقط کارکنان (is_staff) می‌توانند وارد شوند؛ بقیه به صفحه login هدایت می‌شوند.
- `prefetch_related("foods")`: غذاهای هر دسته را از قبل بارگذاری می‌کند (سرعت بیشتر).
- `render(...)`: صفحه HTML را با داده‌ها می‌سازد و برمی‌گرداند.

### ۶.۲ ایجاد غذا
```python
@_staff_api_required
def create_food(request):
    if request.method != "POST": return JsonResponse({...}, status=405)
    name = (request.POST.get("name") or "").strip()
    ...
    food = Food.objects.create(category=category, name=name, ...)
    return JsonResponse({"success": True, "food": {...}})
```
- `@_staff_api_required`: فقط کارکنان مجازند (غیر از این ۴۰۳ می‌گیرند).
- ورودی از فرم (multipart/form-data) خوانده می‌شود.
- اعتبارسنجی: نام خالی نباشد، قیمت منفی نباشد، تصویر معتبر باشد، ...
- در آخر یک غذای جدید در دیتابیس ساخته و JSON برگردانده می‌شود.

### ۶.۳ حذف غذا
```python
@_staff_api_required
def delete_food(request, id):
    food = Food.objects.get(id=id)
    name = food.name
    image = food.image
    food.delete()          # حذف از دیتابیس
    if image: image.delete(save=False)   # حذف فایل تصویر
    return JsonResponse({"success": True, "id": id, "name": name})
```
- تاریخچه سفارش‌ها دست‌نخورده می‌ماند چون OrderItem نام و قیمت را خودش دارد.

### ۶.۴ ویرایش غذا
```python
@_staff_api_required
def update_food(request, id):
    # فقط کلیدهای ارسال‌شده تغییر می‌کنند (partial update)
    data = json.loads(request.body)
    food.name = data.get("name", food.name)
    food.price = data.get("price", food.price)
    ...
    food.save(update_fields=["name", "description", "price", "stock"])
```

### ۶.۵ فعال/غیرفعال کردن غذا (is_active)
```python
@_staff_api_required
def set_food_active(request, id):
    data = json.loads(request.body)
    food.is_active = data.get("is_active")
    food.save(update_fields=["is_active"])   # stock دست‌نخورده می‌ماند
```

### ۶.۶ ایجاد دسته‌بندی
```python
def create_category(request):
    name = (request.POST.get("name") or "").strip()
    # اگر دسته‌ای با همین نام (بدون حساسیت به بزرگی/کوچکی) وجود داشت:
    existing = Category.objects.filter(name__iexact=name).first()
    if existing:
        return JsonResponse({"success": True, "existing": True, "category": {...}})
    category = Category.objects.create(name=name, icon=icon)
    return JsonResponse({"success": True, "existing": False, "category": {...}})
```

### ۶.۷ ثبت سفارش (کاهش موجودی)
```python
@csrf_exempt
@transaction.atomic
def create_order(request):
    # 1. اعتبارسنجی میز (امضای QR)
    # 2. بررسی موجودی هر غذا
    # 3. اگر غذا غیرفعال باشد → رد
    # 4. ایجاد Order و OrderItem
    # 5. کاهش stock غذا: food.stock -= quantity
    # 6. broadcast به داشبورد (real-time)
```

---

## ۷. فرانت‌اند (HTML + JS)

### ۷.۱ صفحه مدیریت منو
فایل `templates/partials/menu_management_panel.html` شامل:
- فرم افزودن/ویرایش غذا (`#mmForm`)
- کارت دسته‌بندی‌ها (`#mmCategoryList`, `#mmCategoryForm`)
- لیست غذاها گروه‌بندی‌شده (`#mmList`)

### ۷.۲ منطق جاوااسکریپت (menu_admin.js)
- `enterEditMode(row)`: فرم را در حالت ویرایش قرار می‌دهد.
- `deleteFood(row)`: بعد از تأیید، `DELETE /api/foods/<id>/delete/` را می‌فرستد.
- `toggleStock(row)`: `PUT {"stock": 0}` یا `{"stock": 1}`.
- `toggleActive(row)`: `POST {"is_active": true/false}`.
- ایجاد دسته: `POST /api/categories/create/` → option جدید به select اضافه می‌شود + ردیف جدید به لیست دسته‌ها.

### ۷.۳ داشبورد آشپزخانه (Dashboard.html)
- برد ۵ ستونه: جدید، در حال آماده‌سازی، آمادهٔ ارسال، ارسال‌شده، منتظر تأیید مشتری.
- اگر کاربر کارمند باشد، پنل مدیریت منو (overlay) نمایش داده می‌شود.

---

## ۸. رابطه بین فایل‌ها (خلاصه)

| فایل | نقش | به چه چیزی وصل است |
|------|-----|---------------------|
| `models.py` | جدول‌های DB | views.py آن را می‌خواند/می‌نویسد |
| `urls.py` | آدرس → تابع | views.py |
| `views.py` | منطق | models.py, templates, JsonResponse |
| `templates/*.html` | صفحات | views.py (context), static JS/CSS |
| `static/js/*.js` | رفتار UI | API endpoints (views.py) |
| `static/css/*.css` | ظاهر | templates |
| `tests*.py` | تست | همهٔ بالا |

---

## ۹. چگونه اجرا کنیم؟

```bash
# نصب کتابخانه‌ها
pip install -r requirements.txt

# اعمال مایگریشن‌ها (ساخت جدول‌ها)
python manage.py migrate

# ایجاد کاربر ادمین
python manage.py createsuperuser

# اجرای سرور
python manage.py runserver 127.0.0.1:8000

# اجرای تست‌ها
python manage.py test
```

سپس مرورگر را باز کن: `http://127.0.0.1:8000/`

---

## ۱۰. نکات مهم برای مبتدی‌ها

1. **هر تغییر در model** نیاز به migration دارد: `python manage.py makemigrations` سپس `migrate`.
2. **CSRF**: فرم‌های POST باید توکن CSRF داشته باشند؛ JS آن را از cookie می‌خواند.
3. **`@_staff_api_required`**: محافظ APIهای مدیریتی؛ بدون آن هر کسی می‌تواند غذا اضافه/حذف کند.
4. **`is_active` vs `stock`**: دو مفهوم جدا؛ یکی مدیریتی، یکی موجودی عددی.
5. **OrderItem نام/قیمت را کپی می‌کند** تا تاریخچه سفارش‌ها همیشه درست بماند.

---

*این راهنما بر اساس کد فعلی پروژه نوشته شده است. برای جزئیات بیشتر، فایل‌های `restaurant/views.py` و `static/js/menu_admin.js` را بخوانید.*
PYEOF
echo done