from django.conf import settings
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse
from django.db import transaction, IntegrityError
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.contrib.auth.decorators import user_passes_test
from datetime import timedelta

import hashlib
import json
import re
import threading
import time
from functools import wraps

from django.db import connection, models

from .models import (
    Category,
    Food,
    Mission,
    Order,
    OrderIdempotencyKey,
    OrderItem,
    _validate_destination_table,
    can_replace_mission_for_order,
)
from .events import broadcast_order_event, broadcast_order_removed
from .validators import validate_optional_phone, validate_table_with_signature
from .qr_generator import (
    verify_table_signature,
    generate_table_signature,
    generate_qr_code_image,
    generate_all_qr_codes,
    cache_table_access,
    get_table_info
)
from . import order_statuses as order_status
from . import mission_statuses as mission_status
from .order_security import (
    TOKEN_HEADER,
    authorize_order_request,
    bind_token_to_session,
    current_token,
    issue_token,
    rate_limited,
    request_carries_customer_token,
)


def serialize_order(order):
    """
    Single source of truth for how an Order looks as JSON — used by the
    REST endpoints, the WebSocket snapshot, and every broadcast event, so
    the kitchen dashboard always renders a consistent shape regardless of
    which channel the data arrived through.
    """
    items = [
        {"name": i.name, "quantity": i.quantity, "price": i.price}
        for i in order.items.all()
    ]

    remaining_seconds = None
    if order.eta_at:
        remaining_seconds = max(int((order.eta_at - timezone.now()).total_seconds()), 0)

    return {
        "id": order.id,
        "table": order.table,
        "seats": order.seats,
        "customer_name": order.customer_name,
        "customer_phone": order.customer_phone,
        "status": order.status,
        "total": order.total,
        "items": items,
        # ISO timestamp — the client computes its own local countdown from
        # this instead of trusting a server-pushed "remaining_seconds" that
        # would go stale between events.
        "eta_at": order.eta_at.isoformat() if order.eta_at else None,
        "remaining_seconds": remaining_seconds,
        "customer_confirmed": order.customer_confirmed,
        "created_at": order.created_at.isoformat(),
    }


# =========================================================
# 🔁 Idempotency — جلوگیری قطعی از ثبت سفارش تکراری
# =========================================================

# ۱۶ تا ۶۴ کاراکتر از حروف/اعداد/‌_-‌ (UUID استاندارد ۳۶ کاراکتر است)
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _order_request_fingerprint(table, seats, foods):
    """
    اثر انگشت نرمال‌شدهٔ درخواست تا یک کلید Idempotency نتواند برای
    سفارش متفاوت (میز/صندلی/اقلام/تعداد) استفاده شود.

    از دادهٔ خام درخواست ساخته می‌شود تا حتی وقتی صندلی‌ها همین حالا
    توسط همان سفارش قبلی اشغال شده‌اند، Retry بتواند همان سفارش را
    بازیابی کند (بررسی تکرار باید قبل از بررسی اشغال صندلی انجام شود).
    """
    normalized_items = []
    for item in foods or []:
        try:
            normalized_items.append([int(item.get("id")), int(item.get("qty", 0))])
        except (ValueError, TypeError):
            continue

    payload = {
        "table": str(table),
        "seats": sorted(str(s).strip() for s in seats),
        "items": sorted(normalized_items, key=lambda pair: (pair[0], pair[1])),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _idempotent_replay_response(request, record, fingerprint):
    """
    پاسخ به درخواستی که کلیدش قبلاً دیده شده است.
    - کلید با درخواست متفاوت → 409 conflict
    - سفارش قبلی موجود → همان نتیجه (بدون ساخت سفارش/رویداد جدید)
    - عملیات قبلی هنوز در جریان → 409 processing

    اعتبارنامهٔ پیگیری همان سفارش دوباره (به‌صورت قطعی و بدون باطل‌شدن)
    به کلاینتی که کلید Idempotency را دارد برگردانده می‌شود؛ Retry شبکه
    نباید باعث گم‌شدن توکن پیگیری شود.
    """
    if record.fingerprint and record.fingerprint != fingerprint:
        return JsonResponse(
            {
                "success": False,
                "code": "idempotency_key_conflict",
                "error": "این شناسهٔ درخواست قبلاً برای سفارش دیگری استفاده شده است."
            },
            status=409
        )

    if record.order_id:
        order = record.order

        # توکن خام ذخیره نمی‌شود؛ از روی nonce همان سفارش بازتولید می‌شود.
        # اگر سفارشی nonce داشته باشد ولی hash نداشته باشد (دادهٔ ناقص/قدیمی)،
        # توکن بازتولیدشده معتبر نخواهد بود و باید تازه صادر شود.
        tracking_token = current_token(order) if order.tracking_token_hash else None
        if not tracking_token:
            tracking_token = issue_token(order)
        bind_token_to_session(request, order.id, tracking_token)

        return JsonResponse(
            {
                "success": True,
                "order_id": order.id,
                "total": order.total,
                "status": order.status,
                "tracking_token": tracking_token,
                "tracking_header": TOKEN_HEADER,
                "replayed": True,
                "message": "این سفارش قبلاً ثبت شده بود."
            }
        )

    return JsonResponse(
        {
            "success": False,
            "code": "processing",
            "error": "درخواست قبلی این سفارش در حال پردازش است. لطفاً کمی بعد دوباره تلاش کنید."
        },
        status=409
    )


# =========================================================
# صفحات
# =========================================================

def customer(request):
    """
    صفحه سفارش مشتری با پشتیبانی از QR Code

    پارامترهای URL:
        ?table=X&sig=SIGNATURE

    QR Code ها شامل امضای امنیتی هستند که از دستکاری جلوگیری می‌کند.
    """
    context = {
        'initial_table': None,
        'table_locked': False,
        'error_message': None
    }

    # خواندن شماره میز از URL
    table_param = request.GET.get('table')
    signature_param = request.GET.get('sig')

    if table_param:
        try:
            table_number = int(table_param)

            # بررسی محدوده معتبر
            if not (1 <= table_number <= 64):
                context['error_message'] = f'شماره میز {table_number} نامعتبر است. میز باید بین 1 تا 64 باشد.'
                return render(request, "customer.html", context)

            # بررسی امضای امنیتی
            if signature_param:
                if verify_table_signature(table_number, signature_param):
                    # QR Code معتبر است
                    context['initial_table'] = table_number
                    context['table_locked'] = True
                    context['table_signature'] = signature_param

                    # کش کردن دسترسی به این میز (1 ساعت)
                    cache_table_access(table_number, 3600)
                else:
                    # امضا نامعتبر است - احتمال دستکاری
                    context['error_message'] = 'QR Code نامعتبر است. لطفاً QR Code میز خود را دوباره اسکن کنید.'
            else:
                # QR Code بدون امضا - به مشتری اجازه انتخاب دستی می‌دهیم
                # اما هشدار می‌دهیم
                context['initial_table'] = table_number
                context['table_locked'] = False
                context['error_message'] = 'لطفاً QR Code روی میز خود را اسکن کنید تا شماره میز قفل شود.'

        except (ValueError, TypeError):
            context['error_message'] = 'پارامتر شماره میز نامعتبر است.'

    return render(request, "customer.html", context)


@user_passes_test(lambda user: user.is_staff, login_url="/admin/login/")
def dashboard(request):
    """
    صفحهٔ آشپزخانه.

    برای کارکنان، پنل «مدیریت منو» هم داخل همین صفحه (در یک لایهٔ بازشو)
    رندر می‌شود؛ کاربران دیگر هیچ بخش مدیریتی در این صفحه نمی‌بینند.
    """
    context = {}

    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated and user.is_staff:
        context["categories"] = (
            Category.objects.prefetch_related("foods").order_by("id")
        )
        context["food_count"] = Food.objects.count()

    return render(request, "dashboard.html", context)


def order_tracking(request, id):
    """
    صفحهٔ مستقل پیگیری سفارش.

    فقط برای مالک سفارش: توکن باید در Session همین مرورگر (که هنگام ثبت
    سفارش ست شده) یا در Header باشد. بدون آن هیچ داده‌ای از سفارش در
    HTML رندر نمی‌شود.
    """
    order = Order.objects.filter(id=id).first()

    if order is None:
        return render(
            request,
            "qr_error.html",
            {"error": "سفارش مورد نظر پیدا نشد."},
            status=404
        )

    authorized, _token = authorize_order_request(request, order)

    if not authorized:
        return render(
            request,
            "qr_error.html",
            {
                "error": (
                    "دسترسی به این سفارش مجاز نیست. اگر مالک این سفارش هستید، "
                    "آن را از صفحهٔ سفارش‌گیری (دکمهٔ پیگیری) باز کنید یا "
                    "QR Code میز را دوباره اسکن کنید."
                )
            },
            status=403
        )

    response = render(
        request,
        "order_tracking.html",
        {
            "order": order
        }
    )
    # صفحهٔ سفارش نباید در کش مرورگر/پروکسی ذخیره شود
    response["Cache-Control"] = "no-store, private"
    return response


# =========================================================
# غذاها
# =========================================================

def get_foods(request):

    categories = Category.objects.prefetch_related(
        "foods"
    )


    result=[]


    for category in categories:

        foods=[]


        for food in category.foods.all():

            # غذای غیرفعال حذف نمی‌شود؛ با `is_active: false` به مشتری
            # داده می‌شود تا کارت آن با برچسب «امروز ارائه نمی‌شود» و بدون
            # دکمهٔ افزودن نمایش داده شود. ثبت سفارشش هم در سرور رد می‌شود.
            foods.append({

                "id": food.id,

                "is_active": food.is_active,

                "name": food.name,

                "description": food.description,

                "price": food.price,

                "stock": food.stock,

                "category": category.name,

                "image":
                    food.image.url
                    if food.image
                    else ""

            })


        result.append({

            "id": category.id,

            "name": category.name,

            "icon": category.icon,

            "foods": foods

        })


    return JsonResponse(
        result,
        safe=False
    )

# =========================================================
# ایجاد غذا + عکس
# =========================================================

# =========================================================
# مدیریت منو (پنل کارکنان)
# =========================================================

MAX_FOOD_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_FOOD_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
FOOD_NAME_MAX_LENGTH = 100
FOOD_DESCRIPTION_MAX_LENGTH = 1000


def _staff_api_required(view_func):
    """
    APIهای مدیریت منو فقط برای کارکنان (is_staff).

    مشتری بدون ورود، حتی با دانستن آدرس API، نمی‌تواند غذا اضافه/حذف کند.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        user = getattr(request, "user", None)

        if user is None or not user.is_authenticated or not user.is_staff:
            return JsonResponse(
                {
                    "success": False,
                    "code": "staff_required",
                    "error": "برای این عملیات باید با حساب کارکنان وارد شوید.",
                },
                status=403,
            )

        return view_func(request, *args, **kwargs)

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def _kitchen_api_required(view_func):
    """
    APIهای آشپزخانه (تغییر وضعیت سفارش) فقط برای کارکنان واردشده.

    سه لایه در یک نقطه:
      1. احراز هویت + مجوز: کاربر باید وارد شده و is_staff باشد؛ ناشناس و
         مشتری حتی با دانستن URL هم نمی‌توانند وضعیت سفارش را عوض کنند.
      2. توکن پیگیری مشتری هیچ‌گاه مجوز عملیات آشپزخانه نیست.
      3. Fail-closed: هر خطای غیرمنتظره در بررسی هویت => 403.

    این دکوراتور فقط لایهٔ مجوز است؛ اعتبارسنجی انتقال وضعیت همچنان در
    `order_statuses.ALLOWED_TRANSITIONS` انجام می‌شود.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        # A customer tracking token is never a kitchen credential. Check it
        # first so the caller gets a precise "wrong credential type" error.
        if request_carries_customer_token(request):
            return JsonResponse(
                {
                    "success": False,
                    "code": "customer_token_not_allowed",
                    "error": "این عملیات برای اعتبارنامهٔ پیگیری مشتری مجاز نیست.",
                },
                status=403,
            )

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or not user.is_staff:
            return JsonResponse(
                {
                    "success": False,
                    "code": "kitchen_auth_required",
                    "error": (
                        "این عملیات فقط برای کارکنان آشپزخانه مجاز است. "
                        "لطفاً با حساب کارکنان وارد شوید."
                    ),
                },
                status=403,
            )

        return view_func(request, *args, **kwargs)

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


def _validate_food_image(image):
    """
    اعتبارسنجی سرورساید تصویر غذا.

    Returns:
        str | None: پیام خطای فارسی، یا None اگر تصویر معتبر/خالی باشد.
    """
    if not image:
        return None

    if image.size > MAX_FOOD_IMAGE_BYTES:
        return "حجم تصویر نباید بیشتر از ۵ مگابایت باشد."

    content_type = (getattr(image, "content_type", "") or "").lower()
    if content_type not in ALLOWED_FOOD_IMAGE_TYPES:
        return "فرمت تصویر مجاز نیست. فقط JPG، PNG یا WebP قابل قبول است."

    # محتوای فایل هم باید واقعاً تصویر باشد (نه فقط پسوند/هدر دروغین)
    try:
        from PIL import Image

        Image.open(image).verify()
        image.seek(0)
    except Exception:
        return "فایل انتخاب‌شده تصویر معتبری نیست."

    return None


@_staff_api_required
def create_food(request):
    """
    ایجاد غذای جدید از پنل مدیریت منو.

    ورودی: multipart/form-data
        name, price, stock, category_id, description (اختیاری), image (اختیاری)
    """
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    name = (request.POST.get("name") or "").strip()
    description = (request.POST.get("description") or "").strip()
    price_raw = request.POST.get("price", "")
    stock_raw = request.POST.get("stock", "0")
    category_id = request.POST.get("category_id")
    image = request.FILES.get("image")

    if not name:
        return JsonResponse(
            {"success": False, "error": "نام غذا وارد نشده است."},
            status=400
        )

    if len(name) > FOOD_NAME_MAX_LENGTH:
        return JsonResponse(
            {
                "success": False,
                "error": f"نام غذا نباید بیشتر از {FOOD_NAME_MAX_LENGTH} کاراکتر باشد."
            },
            status=400
        )

    if len(description) > FOOD_DESCRIPTION_MAX_LENGTH:
        return JsonResponse(
            {
                "success": False,
                "error": f"توضیحات نباید بیشتر از {FOOD_DESCRIPTION_MAX_LENGTH} کاراکتر باشد."
            },
            status=400
        )

    if not category_id:
        return JsonResponse(
            {"success": False, "error": "لطفاً دسته‌بندی غذا را انتخاب کنید."},
            status=400
        )

    try:
        price = int(price_raw)
        stock = int(stock_raw)
        category_id = int(category_id)
    except (ValueError, TypeError):
        return JsonResponse(
            {"success": False, "error": "اطلاعات وارد شده نامعتبر است."},
            status=400
        )

    if price < 0:
        return JsonResponse(
            {"success": False, "error": "قیمت نمی‌تواند منفی باشد."},
            status=400
        )

    if stock < 0:
        return JsonResponse(
            {"success": False, "error": "موجودی نمی‌تواند منفی باشد."},
            status=400
        )

    try:
        category = Category.objects.get(id=category_id)
    except Category.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "دسته‌بندی انتخاب شده وجود ندارد."},
            status=404
        )

    image_error = _validate_food_image(image)
    if image_error:
        return JsonResponse(
            {"success": False, "error": image_error},
            status=400
        )

    # همهٔ اعتبارسنجی‌ها قبل از نوشتن انجام شد؛ درخواست رد‌شده هیچ ردیف/فایلی
    # در دیتابیس و media باقی نمی‌گذارد.
    food = Food.objects.create(
        category=category,
        name=name,
        description=description,
        price=price,
        stock=stock,
        image=image
    )

    return JsonResponse(
        {
            "success": True,
            "food": {
                "id": food.id,
                "name": food.name,
                "description": food.description,
                "category": food.category.name,
                "category_id": food.category_id,
                "category_icon": food.category.icon,
                "price": food.price,
                "stock": food.stock,
                "is_active": food.is_active,
                "image": food.image.url if food.image else ""
            }
        }
    )


# =========================================================
# صفحهٔ مدیریت منو (کارکنان)
# =========================================================

@user_passes_test(lambda user: user.is_staff, login_url="/admin/login/")
def menu_management(request):
    """
    صفحهٔ مدیریت منو: فرم افزودن غذا + فهرست غذای موجود.

    دسته‌ها و غذاها سمت سرور رندر می‌شوند تا صفحه بعد از Refresh هم درست
    باشد و به موفقیت درخواست‌های JS وابسته نباشد.
    """
    categories = Category.objects.prefetch_related("foods").order_by("id")

    return render(
        request,
        "menu_management.html",
        {
            "categories": categories,
            "food_count": Food.objects.count(),
        }
    )


# =========================================================
# ویرایش غذا
# =========================================================

def _parse_non_negative_int(value, label):
    """
    ارزش عددی صحیح و نامنفی را برمی‌گرداند؛ در غیر این صورت پیام خطا.

    Returns:
        tuple[int | None, str | None]
    """
    if isinstance(value, bool):
        return None, f"{label} باید یک عدد صحیح و نامنفی باشد."

    if isinstance(value, int):
        number = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None, f"{label} را وارد کنید."
        try:
            number = int(cleaned)
        except (ValueError, TypeError):
            return None, f"{label} باید یک عدد صحیح و نامنفی باشد."
    else:
        return None, f"{label} باید یک عدد صحیح و نامنفی باشد."

    if number < 0:
        return None, f"{label} نمی‌تواند منفی باشد."

    return number, None


@_staff_api_required
def update_food(request, id):
    """
    ویرایش غذای موجود (بخشی — فقط کلیدهای ارسال‌شده تغییر می‌کنند).

    ورودی: application/json
        name, price, description (و اختیاراً stock)
    """
    if request.method != "PUT":
        return JsonResponse(
            {
                "success": False,
                "error": "PUT only"
            },
            status=405
        )

    try:
        food = Food.objects.get(id=id)
    except Food.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "غذا پیدا نشد."
            },
            status=404
        )

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse(
            {
                "success": False,
                "error": "بدنهٔ درخواست معتبر نیست."
            },
            status=400
        )

    if not isinstance(data, dict):
        return JsonResponse(
            {
                "success": False,
                "error": "بدنهٔ درخواست معتبر نیست."
            },
            status=400
        )

    # ---- نام ----
    name = data.get("name", food.name)
    if not isinstance(name, str):
        return JsonResponse(
            {"success": False, "error": "نام غذا معتبر نیست."},
            status=400
        )

    name = name.strip()
    if not name:
        return JsonResponse(
            {"success": False, "error": "نام غذا نمی‌تواند خالی باشد."},
            status=400
        )

    if len(name) > FOOD_NAME_MAX_LENGTH:
        return JsonResponse(
            {
                "success": False,
                "error": f"نام غذا نباید بیشتر از {FOOD_NAME_MAX_LENGTH} کاراکتر باشد."
            },
            status=400
        )

    # ---- توضیحات ----
    description = data.get("description", food.description)
    if not isinstance(description, str):
        return JsonResponse(
            {"success": False, "error": "توضیحات معتبر نیست."},
            status=400
        )

    description = description.strip()
    if len(description) > FOOD_DESCRIPTION_MAX_LENGTH:
        return JsonResponse(
            {
                "success": False,
                "error": f"توضیحات نباید بیشتر از {FOOD_DESCRIPTION_MAX_LENGTH} کاراکتر باشد."
            },
            status=400
        )

    # ---- قیمت / موجودی ----
    price, price_error = _parse_non_negative_int(data.get("price", food.price), "قیمت")
    if price_error:
        return JsonResponse({"success": False, "error": price_error}, status=400)

    stock, stock_error = _parse_non_negative_int(data.get("stock", food.stock), "موجودی")
    if stock_error:
        return JsonResponse({"success": False, "error": stock_error}, status=400)

    food.name = name
    food.description = description
    food.price = price
    food.stock = stock
    food.save(update_fields=["name", "description", "price", "stock"])

    return JsonResponse(
        {
            "success": True,
            "id": food.id,
            "name": food.name,
            "price": food.price,
            "stock": food.stock,
            "is_active": food.is_active,
            "description": food.description,
            "category": food.category.name if food.category else "",
            "category_id": food.category_id,
            "image": food.image.url if food.image else "",
            "food": {
                "id": food.id,
                "name": food.name,
                "description": food.description,
                "price": food.price,
                "stock": food.stock,
                "is_active": food.is_active,
                "category": food.category.name if food.category else "",
                "category_id": food.category_id,
                "category_icon": food.category.icon if food.category else "",
                "image": food.image.url if food.image else ""
            }
        }
    )


# =========================================================
# فعال / غیرفعال کردن موقت غذا
# =========================================================

@_staff_api_required
def set_food_active(request, id):
    """
    فعال یا غیرفعال کردن موقت یک غذا (بدون حذف رکورد).

    ورودی: application/json
        {"is_active": true | false}

    برخلاف حذف، این عملیات کامل برگشت‌پذیر است: غذا در دیتابیس می‌ماند،
    سفارش‌های قبلی دست‌نخورده‌اند و با یک درخواست دیگر دوباره به منو
    برمی‌گردد. مقدار `stock` (موجودی فیزیکی) هم دست‌نخورده می‌ماند.
    """
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    try:
        food = Food.objects.get(id=id)
    except Food.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "غذا پیدا نشد."
            },
            status=404
        )

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse(
            {
                "success": False,
                "error": "بدنهٔ درخواست معتبر نیست."
            },
            status=400
        )

    if not isinstance(data, dict) or "is_active" not in data:
        return JsonResponse(
            {
                "success": False,
                "error": "وضعیت فعال بودن غذا ارسال نشده است."
            },
            status=400
        )

    raw = data.get("is_active")

    if isinstance(raw, bool):
        is_active = raw
    elif isinstance(raw, str) and raw.strip().lower() in ("true", "false"):
        is_active = raw.strip().lower() == "true"
    else:
        return JsonResponse(
            {
                "success": False,
                "error": "مقدار وضعیت غذا معتبر نیست."
            },
            status=400
        )

    if food.is_active != is_active:
        food.is_active = is_active
        food.save(update_fields=["is_active"])

    return JsonResponse(
        {
            "success": True,
            "is_active": food.is_active,
            "food": {
                "id": food.id,
                "name": food.name,
                "description": food.description,
                "price": food.price,
                "stock": food.stock,
                "is_active": food.is_active,
                "category": food.category.name if food.category else "",
                "category_id": food.category_id,
                "category_icon": food.category.icon if food.category else "",
                "image": food.image.url if food.image else ""
            }
        }
    )


# =========================================================
# حذف غذا
# =========================================================

@_staff_api_required
def delete_food(request, id):
    """
    حذف قطعی غذا (فقط کارکنان).

    اطلاعات سفارش‌های قبلی دست‌نخورده می‌ماند چون OrderItem نام و قیمت را
    خودش نگه می‌دارد و هیچ جدولی به Food ارجاع نمی‌دهد. فایل تصویر هم پاک
    می‌شود تا media/foods بدون استفاده بزرگ نشود.
    """
    if request.method != "DELETE":
        return JsonResponse(
            {
                "success": False,
                "error": "DELETE only"
            },
            status=405
        )

    try:
        food = Food.objects.get(id=id)
    except Food.DoesNotExist:
        return JsonResponse(
            {
                "success": False,
                "error": "این غذا دیگر وجود ندارد.",
                "code": "not_found"
            },
            status=404
        )

    name = food.name
    image = food.image

    food.delete()

    if image:
        try:
            image.delete(save=False)
        except Exception:
            # حذف فایل نباید حذف ردیف را شکست دهد
            pass

    return JsonResponse(
        {
            "success": True,
            "id": id,
            "name": name
        }
    )


# =========================================================
# ثبت سفارش
# کاهش موجودی
# =========================================================

# SQLite فقط یک نویسندهٔ هم‌زمان را تحمل می‌کند و «database is locked»
# می‌دهد. در استقرار تک‌فرایندی، نوشتن سفارش را سریال می‌کنیم؛ Unique
# Constraint همان تضمین نهایی است و این قفل فقط تجربهٔ درخواست دوم را
# از خطای قفل به «همان سفارش قبلی» تبدیل می‌کند.
_SQLITE_WRITE_LOCK = threading.Lock()


def _serialize_sqlite_writes(view_func):
    def wrapper(request, *args, **kwargs):
        if connection.vendor == "sqlite":
            with _SQLITE_WRITE_LOCK:
                return view_func(request, *args, **kwargs)
        return view_func(request, *args, **kwargs)

    wrapper.__name__ = view_func.__name__
    wrapper.__doc__ = view_func.__doc__
    return wrapper


@csrf_exempt
@_serialize_sqlite_writes
@transaction.atomic
def create_order(request):

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    try:

        data = json.loads(request.body)

        table = data.get("table")
        table_signature = data.get("table_signature")  # امضای امنیتی QR Code
        foods = data.get("foods", [])

        # نام فقط برای فاکتور — اختیاریه که فرانت‌اند بفرستدش
        customer_name = (data.get("name") or "").strip()
        customer_phone = (data.get("phone") or "").strip()

        # صندلی‌های انتخاب‌شده روی همین میز (مثل ["T3-top","T3-right"])
        seats = data.get("seats") or []
        if not isinstance(seats, list):
            seats = []

        # =========================================================
        # 🔁 Idempotency — هر عملیات منطقی یک کلید یکتا دارد
        # =========================================================
        # این کلید جدا از table_signature است:
        #   table_signature  → احراز اصالت میز
        #   idempotency_key  → جلوگیری از اجرای دوبارهٔ همان عملیات
        idempotency_key = (data.get("idempotency_key") or "").strip()

        if not idempotency_key:
            return JsonResponse(
                {
                    "success": False,
                    "code": "idempotency_key_required",
                    "error": "شناسهٔ یکتای درخواست (idempotency_key) الزامی است."
                },
                status=400
            )

        if not IDEMPOTENCY_KEY_RE.match(idempotency_key):
            return JsonResponse(
                {
                    "success": False,
                    "code": "idempotency_key_invalid",
                    "error": "شناسهٔ یکتای درخواست معتبر نیست."
                },
                status=400
            )

        # =========================================================
        # 🔐 اعتبارسنجی SERVER-SIDE شماره میز (حیاتی برای امنیت)
        # =========================================================
        # این بخش از تلاش‌های زیر جلوگیری می‌کند:
        # 1. ارسال شماره میز دلخواه از طریق Postman/curl
        # 2. دستکاری شماره میز در DevTools
        # 3. حملات replay با QR Code های قدیمی
        # 4. استفاده از شماره میز خارج از محدوده (>64 یا <1)
        # =========================================================

        try:
            # اعتبارسنجی شماره میز + امضای QR Code
            table = validate_table_with_signature(table, table_signature)
        except ValidationError as e:
            # لاگ کردن تلاش ناموفق برای شناسایی حملات
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Table validation failed: {e.message} "
                f"[table={table}, signature={table_signature}, "
                f"ip={request.META.get('REMOTE_ADDR')}]"
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": str(e.message),
                    "code": e.code
                },
                status=400
            )

        # =========================================================
        # 🔁 Idempotency — بررسی تکرار پیش از بررسی اشغال صندلی
        # =========================================================
        # Retry یک عملیات موفق، صندلی‌های همان سفارش را «اشغال‌شده»
        # می‌بیند؛ بنابراین باید قبل از بررسی تداخل صندلی پاسخ داده شود.
        fingerprint = _order_request_fingerprint(table, seats, foods)

        existing = OrderIdempotencyKey.objects.filter(key=idempotency_key).first()
        if existing is not None:
            return _idempotent_replay_response(request, existing, fingerprint)

        if not isinstance(seats, list) or not seats:

            return JsonResponse(
                {
                    "success": False,
                    "error": "حداقل یک صندلی باید انتخاب شود."
                },
                status=400
            )

        # جلوگیری از رزرو دوباره‌ی صندلی‌هایی که همین الان توسط
        # سفارش فعال دیگری اشغال شده‌اند (وضعیت واقعی، نه فرضی)
        active_orders = Order.objects.exclude(status__in=["done", "rejected"])
        already_taken = set()
        for active in active_orders:
            if active.seats:
                already_taken.update(s.strip() for s in active.seats.split(","))

        conflicting = [s for s in seats if s in already_taken]
        if conflicting:
            return JsonResponse(
                {
                    "success": False,
                    "error": "متأسفانه یکی از صندلی‌های انتخابی همین الان رزرو شد. لطفاً دوباره انتخاب کنید."
                },
                status=409
            )

        # -------------------------
        # اعتبارسنجی شماره تماس
        # -------------------------
        # اگر مشتری شماره تماس وارد کرده است، باید معتبر باشد
        try:
            customer_phone = validate_optional_phone(customer_phone)
            if customer_phone is None:
                customer_phone = ""
        except ValidationError as e:
            return JsonResponse(
                {
                    "success": False,
                    "error": str(e.message)
                },
                status=400
            )

        if not foods:

            return JsonResponse(
                {
                    "success": False,
                    "error": "سبد خرید خالی است."
                },
                status=400
            )

        total = 0

        order_items = []

        # -------------------------
        # بررسی موجودی
        # -------------------------

        for item in foods:

            food_id = item.get("id")

            try:
                quantity = int(item.get("qty", 0))
            except (ValueError, TypeError):
                quantity = 0

            if quantity <= 0:

                return JsonResponse(
                    {
                        "success": False,
                        "error": "تعداد غذا نامعتبر است."
                    },
                    status=400
                )

            try:

                food = Food.objects.select_for_update().get(
                    id=food_id
                )

            except Food.DoesNotExist:

                return JsonResponse(
                    {
                        "success": False,
                        "error":
                            f"غذا با شناسه {food_id} پیدا نشد."
                    },
                    status=404
                )

            # -------------------------
            # بررسی فعال بودن غذا (مدیریتی)
            # -------------------------
            # پنهان کردن غذا در فرانت‌اند کافی نیست؛ اگر غذا بین نمایش منو و
            # ثبت سفارش غیرفعال شده باشد، سرور باید سفارش را رد کند.
            if not food.is_active:

                return JsonResponse(
                    {
                        "success": False,
                        "code": "food_inactive",
                        "error":
                            f"«{food.name}» در حال حاضر در منو ارائه نمی‌شود."
                    },
                    status=400
                )

            # -------------------------
            # بررسی موجودی
            # -------------------------

            if food.stock < quantity:

                return JsonResponse(
                    {
                        "success": False,
                        "error":
                            f"موجودی «{food.name}» کافی نیست. "
                            f"موجودی فعلی: {food.stock}"
                    },
                    status=400
                )

            total += food.price * quantity

            order_items.append(
                {
                    "food": food,
                    "quantity": quantity
                }
            )

        # -------------------------
        # 🔁 Idempotency — ثبت اتمیک کلید
        # -------------------------
        # ثبت کلید با Unique Constraint. اگر دو درخواست هم‌زمان برسند،
        # فقط یکی موفق می‌شود و دومی نتیجهٔ اولی را برمی‌گرداند.
        try:
            with transaction.atomic():
                idempotency_record = OrderIdempotencyKey.objects.create(
                    key=idempotency_key,
                    fingerprint=fingerprint
                )
        except IntegrityError:
            existing = OrderIdempotencyKey.objects.filter(key=idempotency_key).first()
            if existing is None:
                return JsonResponse(
                    {
                        "success": False,
                        "error": "خطا در ثبت شناسهٔ یکتای سفارش."
                    },
                    status=500
                )
            return _idempotent_replay_response(request, existing, fingerprint)

        # -------------------------
        # ایجاد سفارش
        # -------------------------

        order = Order.objects.create(
            table=table,
            total=total,
            status="new",
            customer_name=customer_name,
            customer_phone=customer_phone,
            seats=",".join(seats)
        )

        # -------------------------
        # ایجاد آیتم‌ها
        # + کاهش موجودی
        # -------------------------

        for item in order_items:

            food = item["food"]
            quantity = item["quantity"]

            OrderItem.objects.create(
                order=order,
                name=food.name,
                quantity=quantity,
                price=food.price
            )

            food.stock -= quantity

            food.save(
                update_fields=["stock"]
            )

        idempotency_record.order = order
        idempotency_record.save(update_fields=["order"])

        # -------------------------
        # 🔐 اعتبارنامهٔ پیگیری اختصاصی سفارش
        # -------------------------
        # توکن خام ذخیره نمی‌شود (فقط nonce و hash). همین توکن در Retry
        # با همان idempotency_key دوباره و بدون تغییر برگردانده می‌شود.
        tracking_token = issue_token(order)
        bind_token_to_session(request, order.id, tracking_token)

        # -------------------------
        # پاسخ
        # -------------------------

        broadcast_order_event("new_order", serialize_order(order))

        return JsonResponse(
            {
                "success": True,
                "order_id": order.id,
                "total": total,
                "status": "new",
                "tracking_token": tracking_token,
                "tracking_header": TOKEN_HEADER,
                "message": "سفارش با موفقیت ثبت شد."
            }
        )

    except json.JSONDecodeError:

        return JsonResponse(
            {
                "success": False,
                "error": "اطلاعات ارسال‌شده معتبر نیست."
            },
            status=400
        )

    except Exception as e:

        # خطای غیرمنتظره پس از نوشتن در دیتابیس: کل تراکنش باید برگردد تا
        # سفارش ناقص/کلید سرگردان/کاهش موجودی باقی نماند (Retry سالم بماند).
        transaction.set_rollback(True)

        # رقابت هم‌زمان روی SQLite ممکن است «database is locked» بدهد.
        # اگر درخواست دیگری با همین کلید سفارش را کامل ثبت کرده باشد،
        # به‌جای خطا همان نتیجهٔ سفارش قبلی برگردانده می‌شود.
        if "locked" in str(e).lower():
            seen_key = locals().get("idempotency_key")
            seen_fingerprint = locals().get("fingerprint", "")
            if seen_key:
                for _ in range(10):
                    try:
                        record = (
                            OrderIdempotencyKey.objects
                            .filter(key=seen_key, order__isnull=False)
                            .select_related("order")
                            .first()
                        )
                    except Exception:
                        record = None
                    if record is not None:
                        return _idempotent_replay_response(
                            request, record, seen_fingerprint
                        )
                    time.sleep(0.3)

        return JsonResponse(
            {
                "success": False,
                "error": str(e)
            },
            status=500
        )


# =========================================================
# تشخیص مشتری قبلی بر اساس شماره تلفن
# (برای پرکردن خودکار اسم در صفحه‌ی ورود مشتری)
# =========================================================

def check_customer_phone(request):

    phone = (request.GET.get("phone") or "").strip()

    if not phone:
        return JsonResponse(
            {
                "found": False
            }
        )

    order = (
        Order.objects
        .filter(customer_phone=phone)
        .exclude(customer_name="")
        .order_by("-created_at")
        .first()
    )

    if not order:
        return JsonResponse(
            {
                "found": False
            }
        )

    return JsonResponse(
        {
            "found": True,
            "name": order.customer_name
        }
    )


# =========================================================
# وضعیت واقعی صندلی‌ها (نه فرضی)
# صندلی‌های اشغال‌شده = صندلی‌های سفارش‌های فعال
# (سفارش‌هایی که هنوز تحویل داده نشده یا رد نشده‌اند)
# =========================================================

def get_seats_status(request):

    active_orders = Order.objects.exclude(
        status__in=["done", "rejected"]
    ).exclude(seats="")

    occupied = set()

    for order in active_orders:
        for seat_key in order.seats.split(","):
            seat_key = seat_key.strip()
            if seat_key:
                occupied.add(seat_key)

    return JsonResponse(
        {
            "occupied": sorted(occupied)
        }
    )


# =========================================================
# دریافت سفارش‌ها برای داشبورد
# =========================================================

@_kitchen_api_required
def get_orders(request):

    orders = Order.objects.filter(
        status__in=[
            "new",
            "cooking",
            "awaiting_confirmation",
            "ready",
            "sent",
            "done",
            "rejected"
        ]
    ).order_by("-id")

    result = [serialize_order(order) for order in orders]

    return JsonResponse(
        result,
        safe=False
    )


# =========================================================
# تایید سفارش
# شروع آماده‌سازی
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def accept_order(request, id):

    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    try:

        order = Order.objects.get(id=id)

    except Order.DoesNotExist:

        return JsonResponse(
            {
                "success": False,
                "error": "سفارش پیدا نشد."
            },
            status=404
        )

    # فقط از وضعیت‌های ابتدایی مجاز است (چرخهٔ واحد وضعیت‌ها)
    if not order_status.can_transition(order.status, order_status.STATUS_COOKING):

        return JsonResponse(
            {
                "success": False,
                "error": "این سفارش قابل تایید نیست."
            },
            status=400
        )

    # -------------------------
    # شروع آماده‌سازی
    # -------------------------

    order.status = "cooking"

    order.cooking_started_at = timezone.now()

        # پیش‌فرض تایمر آماده‌سازی/تحویل: ۵ دقیقه — با دکمه‌های +5/-5 قابل تنظیم است
    order.eta_at = timezone.now() + timedelta(minutes=5)

    order.save(
        update_fields=["status", "cooking_started_at", "eta_at"]
    )

    broadcast_order_event("order_updated", serialize_order(order))

    return JsonResponse(
        {
            "success": True,
            "status": "cooking",
            "eta_at": order.eta_at.isoformat(),
            "message":
                "سفارش تایید شد و وارد مرحله آماده‌سازی شد."
        }
    )


# =========================================================
# رد سفارش
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def reject_order(request, id):

    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":

        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    try:

        order = Order.objects.get(id=id)

    except Order.DoesNotExist:

        return JsonResponse(
            {
                "success": False,
                "error": "سفارش پیدا نشد."
            },
            status=404
        )

    if not order_status.can_transition(order.status, order_status.STATUS_REJECTED):

        return JsonResponse(
            {
                "success": False,
                "error":
                    "این سفارش دیگر قابل رد کردن نیست."
            },
            status=400
        )

    order.status = "rejected"

    order.save(
        update_fields=["status"]
    )

    broadcast_order_removed(order.id)

    return JsonResponse(
        {
            "success": True,
            "status": "rejected",
            "message":
                "سفارش توسط رستوران رد شد."
        }
    )


# =========================================================
# آماده شدن سفارش
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def ready_order(request, id):
    """
    تأیید آماده‌بودن سفارش توسط آشپزخانه.

    پس از تأیید موفق، یک مأموریت ربات به‌صورت خودکار و
    Idempotent برای این سفارش ایجاد می‌شود.
    """
    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST only"},
            status=405,
        )

    try:
        order = Order.objects.select_for_update().get(id=id)
    except Order.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "سفارش پیدا نشد."},
            status=404,
        )

    was_ready = order.status == order_status.STATUS_READY

    # اگر سفارش هنوز ready نشده، انتقال باید مجاز باشد.
    if not was_ready and not order_status.can_transition(
        order.status, order_status.STATUS_READY
    ):
        return JsonResponse(
                        {
                "success": False,
                "error": "این سفارش هنوز در حال آماده‌سازی نیست."
            },
            status=400,
        )


    if not was_ready:
        order.status = order_status.STATUS_READY
        order.save(update_fields=["status"])

    # Destination must be a valid table number (server-authoritative).
    destination = str(order.table)
    try:
        _validate_destination_table(destination)
    except ValidationError as e:
        return JsonResponse(
            {
                "success": False,
                "error": str(e.message),
                "code": "invalid_destination_table",
            },
            status=400,
        )

    # ایجاد Idempotent مأموریت (OneToOne در DB).
    # اگر مأموریتی برای سفارش ready بدون مأموریت وجود داشته باشد، ساخته می‌شود.
    # کارکنان نباید بتوانند مأموریت تکراری برای سفارشی که هنوز در جریان است رو بسازن.
    existing_mission = getattr(order, "mission", None)
    # Calling "ready" twice is idempotent: the existing mission is returned
    # unchanged. Only block when we would actually create a *replacement*
    # mission for an order whose mission is still active.
    if not was_ready and not can_replace_mission_for_order(existing_mission):
        return JsonResponse(
            {
                "success": False,
                "error": "این سفارش نمی‌تواند مأموریت تکراری داشته باشد.",
                "code": "mission_replace_forbidden",
            },
            status=409,
        )

    try:
        with transaction.atomic():
            mission, created = Mission.objects.get_or_create(
                order=order,
                defaults={
                    "destination_table": destination,
                    "status": Mission.STATUS_PENDING,
                },
            )
    except IntegrityError:
        mission = Mission.objects.get(order=order)
        created = False

    if created:
        # پخش رویداد ایجاد مأموریت برای증액 بدنهٔ ربات
        broadcast_order_event(
            "mission_updated",
            {
                "mission_code": mission.mission_code,
                "mission_uid": str(mission.mission_uid),
                "status": mission.status,
                "order_id": order.id,
            },
        )

    if not was_ready:
        broadcast_order_event("order_updated", serialize_order(order))

    return JsonResponse(
        {
            "success": True,
            "status": "ready",
            "message": (
                "سفارش آماده شد و مأموریت ربات ایجاد شد."
                if created
                else "سفارش آماده شد."
            ),
            "mission_id": str(mission.mission_uid),
            "mission_status": mission.status,
            "mission_created": created,
        }
    )


# =========================================================
# ارسال سفارش به ربات
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def send_order(request, id):
    """
    ارسال سفارش به ربات: از وضعیت ready به sent و پیشبرد مأموریت به in_progress.

    یک مأموریت تازه وضعیت pending دارد (یا ممکن است اصلاً ساخته نشده باشد).
    ارسال فرمان، همان لحظهٔ تحویل به ربات است؛ بنابراین مأموریت مستقیماً از
    pending/queued/assigned به in_progress میرود. ارسال دوباره رد میشود تا
    فرمان دوباره صادر نشود و eta_at بازنویسی نگردد.
    """
    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":
        return JsonResponse(
            {"success": False, "error": "POST only"},
            status=405
        )

    try:
        order = Order.objects.select_for_update().get(id=id)
    except Order.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "سفارش پیدا نشد."},
            status=404
        )

    # Only a ready order may be dispatched. This also rejects a second send,
    # so the travel deadline (eta_at) is never rewritten.
    if order.status != order_status.STATUS_READY:
        return JsonResponse(
            {"success": False, "error": "فقط سفارش آماده را می‌توان ارسال کرد."},
            status=400
        )

    mission = getattr(order, "mission", None)

    # A mission is created on ready_order, but hedge against older/partial
    # data: create it here if it is missing so dispatch never crashes.
    if mission is None:
        destination = str(order.table)
        try:
            _validate_destination_table(destination)
            mission = Mission.objects.create(
                order=order,
                destination_table=destination,
                status=Mission.STATUS_PENDING,
            )
        except (ValidationError, IntegrityError):
            return JsonResponse(
                {"success": False, "error": "ایجاد مأموریت ارسال ناموفق بود."},
                status=400
            )

    # The robot has not necessarily "claimed" the mission (assigned) yet;
    # dispatching it is the moment execution starts.
    if mission.status not in (
        Mission.STATUS_PENDING,
        Mission.STATUS_QUEUED,
        Mission.STATUS_ASSIGNED,
    ):
        return JsonResponse(
            {"success": False, "error": "این مأموریت قابل ارسال نیست."},
            status=400
        )

    order.status = order_status.STATUS_SENT

    # زمان تقریبی رسیدن ربات به میز. پس از این زمان سفارش به «در انتظار
    # تأیید تحویل» می‌رود؛ هیچ‌گاه مستقیماً «تحویل‌شده» نمی‌شود.
    travel_seconds = max(int(getattr(settings, "ROBOT_DELIVERY_SECONDS", 60)), 1)
    order.eta_at = timezone.now() + timedelta(seconds=travel_seconds)

    order.save(update_fields=["status", "eta_at"])

    broadcast_order_event("order_updated", serialize_order(order))

    # ارسال سفارش → پیشبرد مأموریت به in_progress
    mission.status = Mission.STATUS_IN_PROGRESS
    mission.started_at = timezone.now()
    mission.save(update_fields=["status", "started_at"])
    broadcast_order_event(
        "mission_updated",
        {
            "mission_code": mission.mission_code,
            "mission_uid": str(mission.mission_uid),
            "status": mission.status,
            "started_at": mission.started_at.isoformat(),
            "order_id": order.id,
        },
    )

    return JsonResponse(
        {
            "success": True,
            "status": order.status,
            "eta_at": order.eta_at.isoformat(),
            "message": "سفارش برای ربات ارسال شد.",
            "mission_code": mission.mission_code,
        }
    )


# =========================================================
# ارسال مأموریت به ربات (Mission Dispatch API)
# =========================================================

from .mission_dispatch import DispatchError, dispatch_mission


@_kitchen_api_required
def dispatch_mission_api(request):
    """POST /api/missions/dispatch/ — ارسال استاندارد فرمان حرکت به ربات.

    ورودی (حداقل): {"mission_uid": "<uuid>"} یا {"mission_code": "MSN-000001"}
    اختیاری: {"robot_id": "..."}، {"parameters": {...}}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "code": "method_not_allowed",
                             "error": "POST only"}, status=405)

    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, TypeError):
        return JsonResponse({"success": False, "code": "invalid_json",
                             "error": "بدنه درخواست نامعتبر است."}, status=400)

    try:
        result = dispatch_mission(
            mission_uid=data.get("mission_uid"),
            mission_code=data.get("mission_code"),
            robot_id=data.get("robot_id"),
            parameters=data.get("parameters"),
        )
    except DispatchError as exc:
        return JsonResponse({"success": False, "code": exc.code, "error": exc.message},
                            status=exc.http_status)

    return JsonResponse(result, status=200)


# =========================================================
# مأموریت‌های ربات (Mission API)
# =========================================================

@_kitchen_api_required
def list_missions(request):
    """فهرست مأموریت‌های ربات با امکان فیلتر و جست‌وجو."""
    if request.method != "GET":
        return JsonResponse({"success": False, "error": "GET only"}, status=405)

    # پارامترهای فیلتر و جست‌وجو
    status_filter = request.GET.get("status", "").strip()
    table_filter = request.GET.get("table", "").strip()
    search = request.GET.get("q", "").strip()
    robot_filter = request.GET.get("robot", "").strip()

    missions = Mission.objects.select_related("order").all()

    if status_filter and status_filter in dict(Mission.STATUS_CHOICES).keys():
        missions = missions.filter(status=status_filter)

    if table_filter:
        missions = missions.filter(destination_table=table_filter)

    if robot_filter:
        missions = missions.filter(robot_id=robot_filter)

    if search:
        missions = missions.filter(
            models.Q(mission_code__icontains=search)
            | models.Q(mission_uid__icontains=search)
            | models.Q(order__id__icontains=search)
            | models.Q(destination_table__icontains=search)
        )

    result = []
    for m in missions:
        result.append({
            "mission_code": m.mission_code,
            "mission_uid": str(m.mission_uid),
            "status": m.status,
            "destination_table": m.destination_table,
            "robot_id": m.robot_id,
            "order_id": m.order_id,
            "order_table": m.order.table,
            "created_at": m.created_at.isoformat(),
            "started_at": m.started_at.isoformat() if m.started_at else None,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
        })

    return JsonResponse({
        "success": True,
        "missions": result,
        "count": len(result),
    })


@_kitchen_api_required
def get_mission_detail(request, mission_uid):
    """جزئیات یک مأموریت بر اساس mission_uid."""
    if request.method != "GET":
        return JsonResponse({"success": False, "error": "GET only"}, status=405)

    try:
        mission = Mission.objects.select_related("order").get(mission_uid=mission_uid)
    except Mission.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "مأموریت پیدا نشد."}, status=404
        )

    return JsonResponse({
        "success": True,
        "mission": {
            "mission_code": mission.mission_code,
            "mission_uid": str(mission.mission_uid),
            "status": mission.status,
            "destination_table": mission.destination_table,
            "robot_id": mission.robot_id,
            "order_id": mission.order_id,
            "order": {
                "id": mission.order.id,
                "table": mission.order.table,
                "customer_name": mission.order.customer_name,
                "total": mission.order.total,
                "status": mission.order.status,
                "created_at": mission.order.created_at.isoformat(),
            },
            "created_at": mission.created_at.isoformat(),
            "started_at": mission.started_at.isoformat() if mission.started_at else None,
            "completed_at": mission.completed_at.isoformat() if mission.completed_at else None,
            "error_message": mission.error_message,
        },
    })


@_kitchen_api_required
def update_mission_status(request, mission_uid):
    """تغییر وضعیت مأموریت (رویداد از زبان ربات).

    بدنه: {"status": "assigned|in_progress|completed|failed|cancelled"}
    optional: {"robot_id": "..."}, {"error_message": "..."}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        mission = Mission.objects.select_for_update().get(mission_uid=mission_uid)
    except Mission.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "مأموریت پیدا نشد."},
            status=404
        )

    try:
        data = json.loads(request.body or b"{}")
    except (ValueError, TypeError):
        return JsonResponse(
            {"success": False, "error": "بدنه نامعتبر."}, status=400
        )

    new_status = data.get("status", "").strip()
    # Validate the status value first: an unknown status is a bad request
    # (400), not a transition conflict.
    if new_status not in dict(Mission.STATUS_CHOICES):
        return JsonResponse(
            {"success": False, "error": f"وضعیت نامعتبر: {new_status!r}"},
            status=400
        )
    if not mission_status.can_transition(mission.status, new_status):
        return JsonResponse(
            {
                "success": False,
                "error": f"انتقال غیرمجاز: {mission.status!r} → {new_status!r}",
            },
            status=409,
        )

    mission.status = new_status
    if new_status == Mission.STATUS_ASSIGNED and not mission.robot_id:
        mission.robot_id = (data.get("robot_id") or "").strip() or mission.robot_id
    if new_status == Mission.STATUS_IN_PROGRESS and not mission.started_at:
        mission.started_at = timezone.now()
    if new_status == Mission.STATUS_COMPLETED and not mission.completed_at:
        mission.completed_at = timezone.now()
    if new_status in (Mission.STATUS_FAILED, Mission.STATUS_CANCELLED):
        if data.get("error_message"):
            mission.error_message = (data["error_message"] or "").strip()
        if not mission.completed_at:
            mission.completed_at = timezone.now()

    mission.save(update_fields=[
        "status",
        "robot_id",
        "started_at",
        "completed_at",
        "error_message",
    ])

    broadcast_order_event(
        "mission_updated",
        {
            "mission_code": mission.mission_code,
            "mission_uid": str(mission.mission_uid),
            "status": mission.status,
            "robot_id": mission.robot_id,
            "order_id": mission.order_id,
        },
    )

    return JsonResponse({
        "success": True,
        "mission_code": mission.mission_code,
        "status": mission.status,
        "message": "وضعیت مأموریت به‌روزرسانی شد.",
    })


@_kitchen_api_required
@transaction.atomic
def cancel_mission(request, mission_uid):
    """لغو مأموریت (فقط از وضعیت‌های فعال).

    بدنه اختیاری: {"reason": "..."}
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    # Optional JSON body: {"reason": "..."}. Be tolerant of an empty body.
    try:
        data = json.loads(request.body or b"{}")
        if not isinstance(data, dict):
            data = {}
    except (json.JSONDecodeError, TypeError):
        data = {}

    try:
        mission = Mission.objects.select_for_update().get(mission_uid=mission_uid)
    except Mission.DoesNotExist:
        return JsonResponse(
            {"success": False, "error": "مأموریت پیدا نشد."},
            status=404
        )

    if not mission_status.can_transition(mission.status, Mission.STATUS_CANCELLED):
        return JsonResponse(
            {
                "success": False,
                "error": (
                    f"لغو از وضعیت {mission.status!r} غیرمجاز است. "
                    f"فقط مأموریت‌های pending/queued/assigned/in_progress قابل لغو هستند."
                ),
            },
            status=409
        )

    mission.status = Mission.STATUS_CANCELLED
    if data.get("reason"):
        mission.error_message = str(data["reason"] or "").strip()
    mission.completed_at = timezone.now()
    mission.save(update_fields=[
        "status",
        "error_message",
        "completed_at",
    ])

    broadcast_order_event(
        "mission_updated",
        {
            "mission_code": mission.mission_code,
            "mission_uid": str(mission.mission_uid),
            "status": mission.status,
            "robot_id": mission.robot_id,
            "order_id": mission.order_id,
            "cancelled": True,
        },
    )

    return JsonResponse({
        "success": True,
        "mission_code": mission.mission_code,
        "status": mission.status,
        "message": "مأموریت لغو شد.",
    })


# =========================================================
# دریافت دسته‌بندی‌ها
# =========================================================

def get_categories(request):
    categories = Category.objects.all()
    result = []
    for cat in categories:
        result.append({
            "id": cat.id,
            "name": cat.name,
            "icon": cat.icon
        })
    return JsonResponse(result, safe=False)


# =========================================================
# ایجاد دسته‌بندی جدید
# =========================================================

CATEGORY_NAME_MAX_LENGTH = 100


@_staff_api_required
def create_category(request):
    """
    ایجاد دسته‌بندی جدید (فقط کارکنان).

    اگر دسته‌ای با همین نام (پس از trim، بدون حساسیت به بزرگی/کوچکی)
    وجود داشته باشد، همان دسته با existing=true برگردانده می‌شود تا
    دو دستهٔ هم‌نام و غذای پراکنده ساخته نشود.
    """
    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    name = (request.POST.get("name") or "").strip()
    icon = (request.POST.get("icon") or "🍽").strip() or "🍽"

    if not name:
        return JsonResponse(
            {"success": False, "error": "نام دسته وارد نشده است."},
            status=400
        )

    if len(name) > CATEGORY_NAME_MAX_LENGTH:
        return JsonResponse(
            {
                "success": False,
                "error": f"نام دسته نباید بیشتر از {CATEGORY_NAME_MAX_LENGTH} کاراکتر باشد."
            },
            status=400
        )

    if len(icon) > 50:
        icon = icon[:50]

    existing = Category.objects.filter(name__iexact=name).first()
    if existing is not None:
        return JsonResponse(
            {
                "success": True,
                "existing": True,
                "category": {
                    "id": existing.id,
                    "name": existing.name,
                    "icon": existing.icon
                }
            }
        )

    try:
        category = Category.objects.create(name=name, icon=icon)
    except Exception:
        return JsonResponse(
            {"success": False, "error": "ایجاد دسته‌بندی انجام نشد."},
            status=500
        )

    return JsonResponse(
        {
            "success": True,
            "existing": False,
            "category": {
                "id": category.id,
                "name": category.name,
                "icon": category.icon
            }
        }
    )


# =========================================================
# دریافت اطلاعات یک سفارش
# برای صفحه Tracking مشتری
# =========================================================

# =========================================================
# 🔐 پیگیری امن سفارش — فقط برای مالک سفارش
# =========================================================

# پاسخ یکسان برای «سفارش وجود ندارد» و «توکن نامعتبر است»؛ این‌طور
# شناسهٔ سفارش‌ها قابل شمارش (enumeration) نیست.
TRACKING_NOT_FOUND = {
    "success": False,
    "code": "order_not_found_or_unauthorized",
    "error": "سفارش مورد نظر یافت نشد یا دسترسی به آن مجاز نیست.",
}


def _customer_token_blocked(request):
    """
    توکن پیگیری مشتری هیچ‌گاه مجوز عملیات آشپزخانه/مدیریت نیست.

    اگر درخواستی با توکن پیگیری مشتری به APIهای تغییر وضعیت برسد، رد
    می‌شود تا اعتبارنامهٔ مشتری به ابزار کنترل سفارش/ربات تبدیل نشود.
    """
    if request_carries_customer_token(request):
        return JsonResponse(
            {
                "success": False,
                "code": "customer_token_not_allowed",
                "error": "این عملیات برای اعتبارنامهٔ پیگیری مشتری مجاز نیست."
            },
            status=403
        )
    return None


def _client_ip(request):
    return request.META.get("REMOTE_ADDR") or "unknown"


def _no_store(response):
    response["Cache-Control"] = "no-store, private"
    return response


def _tracking_denied_response():
    return _no_store(JsonResponse(TRACKING_NOT_FOUND, status=404))


def _rate_limited_response():
    return _no_store(
        JsonResponse(
            {
                "success": False,
                "code": "rate_limited",
                "error": "تعداد درخواست‌ها بیش از حد مجاز است. لطفاً کمی بعد دوباره تلاش کنید."
            },
            status=429
        )
    )


def _order_progress(order):
    """درصد پیشرفت و زمان باقی‌مانده — هر دو از منبع واحد وضعیت‌ها."""
    return (
        order_status.progress_for(order),
        order_status.remaining_seconds_for(order),
    )


def _tracking_steps(order):
    """مراحل پیگیری مشتری؛ از همان تعریف مشترک Backend."""
    return order_status.steps_for(order.status)


def _serialize_customer_order(order):
    """
    Serializer مخصوص «پیگیری مشتری» — جدا از serialize_order مدیریتی.

    فقط اطلاعاتی که مشتری برای پیگیری سفارش خودش لازم دارد برمی‌گردد:
    شمارهٔ سفارش، میز، وضعیت، اقلام، مبلغ و زمان‌ها. اطلاعات داخلی
    (نام/تلفن مشتری، صندلی‌ها، توکن‌ها، داده‌های آشپزخانه و ربات) هیچ‌گاه
    در این پاسخ نیست.
    """
    progress, remaining_seconds = _order_progress(order)

    items = [
        {
            "name": item.name,
            "quantity": item.quantity,
            "price": item.price,
        }
        for item in order.items.all()
    ]

    return {
        "success": True,
        "id": order.id,
        "table": order.table,
        "status": order.status,
        "status_label": order_status.status_label(order.status),
        "total": order.total,
        "items": items,
        "item_count": sum(item["quantity"] for item in items),
        "progress": progress,
        "remaining_seconds": remaining_seconds,
        "steps": _tracking_steps(order),
        "eta_at": order.eta_at.isoformat() if order.eta_at else None,
        "customer_confirmed": order.customer_confirmed,
        "created_at": order.created_at.isoformat(),
        "rejected": order.status == order_status.STATUS_REJECTED,
        "cancelled": order_status.is_cancelled(order.status),
        "unknown_status": order_status.is_unknown(order.status),
    }


def get_order(request, id):
    """
    دریافت اطلاعات یک سفارش برای پیگیری مشتری.

    امنیت:
      - شناسهٔ سفارش به‌تنهایی هیچ مجوزی نمی‌دهد.
      - توکن پیگیری فقط از Header (X-Order-Token / Authorization: Bearer)
        یا Session همین مرورگر خوانده می‌شود؛ هرگز از URL.
      - توکن سفارش دیگر و توکن نامعتبر هر دو همان 404 عمومی هستند.
      - هر IP سقف درخواست (و سقف سخت‌تر برای تلاش‌های ناموفق) دارد.
      - این Endpoint فقط خواندن است و هیچ تغییری در سفارش ایجاد نمی‌کند.
    """
    if request.method not in ("GET", "HEAD"):
        return JsonResponse(
            {"success": False, "error": "GET only"},
            status=405
        )

    ip = _client_ip(request)

    if rate_limited("track-order", ip, limit=60, window=60):
        return _rate_limited_response()

    order = (
        Order.objects
        .prefetch_related("items")
        .filter(id=id)
        .first()
    )

    authorized = False
    if order is not None:
        authorized, _token = authorize_order_request(request, order)

    if not authorized:
        # تلاش‌های ناموفق (حدس توکن/شناسه) سقف سخت‌تری دارند
        if rate_limited("track-order-fail", ip, limit=20, window=60):
            return _rate_limited_response()
        return _tracking_denied_response()

    return _no_store(JsonResponse(_serialize_customer_order(order)))


# =========================================================
# تنظیم تایمر آماده‌سازی/تحویل (+۵ / -۵ دقیقه)
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def adjust_order_timer(request, id):

    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        order = Order.objects.get(id=id)
    except Order.DoesNotExist:
        return JsonResponse({"success": False, "error": "سفارش پیدا نشد."}, status=404)

    if order.status != order_status.STATUS_COOKING or not order.eta_at:
        return JsonResponse(
            {"success": False, "error": "این سفارش الان قابل تنظیم تایمر نیست."},
            status=400
        )

    try:
        data = json.loads(request.body)
        delta_minutes = int(data.get("delta_minutes", 0))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({"success": False, "error": "مقدار نامعتبر است."}, status=400)

    new_eta = order.eta_at + timedelta(minutes=delta_minutes)

    # یه -۵ نباید تایمر رو فوری صفر/منفی کنه؛ حداقل ۱۰ ثانیه نگه می‌داریم
    min_eta = timezone.now() + timedelta(seconds=10)
    if new_eta < min_eta:
        new_eta = min_eta

    order.eta_at = new_eta
    order.save(update_fields=["eta_at"])

    broadcast_order_event("order_updated", serialize_order(order))

    return JsonResponse({"success": True, "eta_at": order.eta_at.isoformat()})


# =========================================================
# پاسخ مشتری به «سفارشت رو گرفتی؟» بعد از اتمام تایمر
# =========================================================

@_serialize_sqlite_writes
@transaction.atomic
def customer_confirm_order(request, id):

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        order = Order.objects.get(id=id)
    except Order.DoesNotExist:
        return JsonResponse({"success": False, "error": "سفارش پیدا نشد."}, status=404)

    # فقط مالک سفارش (با توکن همان سفارش) می‌تواند پاسخ تأیید بدهد
    authorized, _token = authorize_order_request(request, order)
    if not authorized:
        if rate_limited("track-confirm-fail", _client_ip(request), limit=20, window=60):
            return _rate_limited_response()
        return _tracking_denied_response()

    if not order_status.can_transition(order.status, order_status.STATUS_DONE):
        return JsonResponse(
            {"success": False, "error": "این سفارش الان منتظر تأیید نیست."},
            status=400
        )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        data = {}

    received = bool(data.get("received"))
    order.customer_confirmed = received

    if received:
        order.status = "done"
        order.save(update_fields=["customer_confirmed", "status"])
        broadcast_order_removed(order.id)
    else:
        # می‌مونه رو awaiting_confirmation ولی با پرچم قرمز روی داشبورد
        # آشپزخانه، تا شف دستی بررسی/رسیدگی کنه.
        order.save(update_fields=["customer_confirmed"])
        broadcast_order_event("order_updated", serialize_order(order))

    return JsonResponse({"success": True, "status": order.status})


# =========================================================
# رسیدگی شف به سفارشی که مشتری گفته «نرسیده»
# =========================================================

@_kitchen_api_required
@_serialize_sqlite_writes
@transaction.atomic
def resolve_order_issue(request, id):

    blocked = _customer_token_blocked(request)
    if blocked is not None:
        return blocked

    if request.method != "POST":
        return JsonResponse({"success": False, "error": "POST only"}, status=405)

    try:
        order = Order.objects.get(id=id)
    except Order.DoesNotExist:
        return JsonResponse({"success": False, "error": "سفارش پیدا نشد."}, status=404)

    if order.status != "awaiting_confirmation" or order.customer_confirmed is not False:
        return JsonResponse(
            {"success": False, "error": "این سفارش الان مورد بررسی نداره."},
            status=400
        )

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        data = {}

    action = data.get("action")

    if action == "retry":
        order.status = "cooking"
        order.eta_at = timezone.now() + timedelta(minutes=5)
        order.customer_confirmed = None
        order.save(update_fields=["status", "eta_at", "customer_confirmed"])
        broadcast_order_event("order_updated", serialize_order(order))
    elif action == "done":
        order.status = "done"
        order.save(update_fields=["status"])
        broadcast_order_removed(order.id)
    else:
        return JsonResponse({"success": False, "error": "عملیات نامعتبر."}, status=400)

    return JsonResponse({"success": True, "status": order.status})


# =========================================================
# QR Code Views
# =========================================================

def qr_code_image(request, table_number):
    """
    تولید تصویر QR Code برای یک میز خاص

    استفاده:
        /qr/<table_number>/
        مثال: /qr/1/ برای میز شماره 1

    خروجی:
        تصویر PNG حاوی QR Code با امضای امنیتی
    """
    try:
        table_number = int(table_number)

        # بررسی محدوده معتبر
        if not (1 <= table_number <= 64):
            return HttpResponse(
                f'شماره میز {table_number} نامعتبر است.',
                status=400
            )

        # تولید تصویر QR Code
        image_bytes = generate_qr_code_image(table_number)

        # بازگشت تصویر
        return HttpResponse(
            image_bytes,
            content_type='image/png'
        )

    except (ValueError, TypeError):
        return HttpResponse(
            'پارامتر شماره میز نامعتبر است.',
            status=400
        )
    except Exception as e:
        return HttpResponse(
            f'خطا در تولید QR Code: {str(e)}',
            status=500
        )


def qr_code_page(request, table_number):
    """
    صفحه نمایش QR Code برای چاپ

    استفاده:
        /qr/<table_number>/page/
        مثال: /qr/1/page/ برای صفحه چاپ میز 1

    خروجی:
        صفحه HTML حاوی QR Code و اطلاعات میز
    """
    try:
        table_number = int(table_number)

        # بررسی محدوده معتبر
        if not (1 <= table_number <= 64):
            return render(
                request,
                "qr_error.html",
                {
                    'error': f'شماره میز {table_number} نامعتبر است.'
                }
            )

        # دریافت اطلاعات میز
        table_info = get_table_info(table_number)

        context = {
            'table_number': table_number,
            'table_info': table_info,
            'qr_url': f'/qr/{table_number}/',
        }

        return render(request, "qr_page.html", context)

    except (ValueError, TypeError):
        return render(
            request,
            "qr_error.html",
            {
                'error': 'پارامتر شماره میز نامعتبر است.'
            }
        )
    except Exception as e:
        return render(
            request,
            "qr_error.html",
            {
                'error': f'خطا: {str(e)}'
            }
        )


@user_passes_test(lambda user: user.is_staff, login_url="/admin/login/")
def qr_codes_dashboard(request):
    """
    داشبورد مدیریت QR Codeها

    استفاده:
        /qr/dashboard/

    خروجی:
        صفحه HTML حاوی لیست تمام میزها با لینک‌های QR Code
    """
    tables = []
    for i in range(1, 65):
        table_info = get_table_info(i)
        tables.append({
            'number': i,
            'info': table_info,
            'qr_url': f'/qr/{i}/',
            'page_url': f'/qr/{i}/page/',
        })

    context = {
        'tables': tables,
    }

    return render(request, "qr_dashboard.html", context)


@_staff_api_required
def generate_all_qr_codes_view(request):
    """
    تولید تمام QR Codeها به‌صورت یک‌جا (برای دانلود ZIP)

    استفاده:
        POST /qr/generate-all/

    خروجی:
        فایل ZIP حاوی تمام تصاویر QR Code
    """
    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "error": "POST only"
            },
            status=405
        )

    try:
        # تولید فایل ZIP
        zip_buffer = generate_all_qr_codes()

        # بازگشت فایل ZIP
        response = HttpResponse(
            zip_buffer.getvalue(),
            content_type='application/zip'
        )
        response['Content-Disposition'] = 'attachment; filename="qr_codes.zip"'

        return response

    except Exception as e:
        return JsonResponse(
            {
                "success": False,
                "error": str(e)
            },
            status=500
        )