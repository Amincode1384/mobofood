"""
اعتبارنامهٔ پیگیری سفارش (Order Tracking Token) — بدون نیاز به حساب کاربری.

طراحی:

- برای هر سفارش یک ``nonce`` تصادفی (۳۲ بایت از ``secrets``) ذخیره می‌شود.
- توکنی که به مشتری داده می‌شود از HMAC-SHA256 روی همان nonce با کلید
  محرمانهٔ سمت سرور ساخته می‌شود:

      token = base64url( nonce_bytes || HMAC(key, nonce_bytes) )

  بنابراین:
    * توکن قابل حدس نیست (۳۲ بایت آنتروپی + امضای سرور)،
    * به شمارهٔ سفارش/میز/تلفن مشتری وابسته نیست،
    * توکن خام هیچ‌جا ذخیره نمی‌شود؛ فقط ``sha256(token)`` در دیتابیس است،
    * در Retry همان سفارش (idempotency) توکن دقیقاً از روی nonce بازتولید
      می‌شود، پس اعتبارنامهٔ قبلی باطل/گم نمی‌شود.

توکن فقط از طریق Header (``X-Order-Token`` یا ``Authorization: Bearer``)
یا Session سمت سرور (HttpOnly cookie) خوانده می‌شود؛ هیچ‌گاه از URL.
"""

import base64
import hashlib
import hmac
import secrets
import time

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

# ---------------------------------------------------------
# کلید و مشتق‌سازی
# ---------------------------------------------------------

TOKEN_BYTES = 32                 # ۲۵۶ بیت آنتروپی
TOKEN_HEADER = "X-Order-Token"
_PURPOSE = b"robotic-restaurant:order-tracking:v1"
SESSION_KEY = "order_tracking_tokens"
SESSION_MAX_ENTRIES = 20


def _tracking_key() -> bytes:
    secret = getattr(settings, "ORDER_TRACKING_SECRET", None) or settings.SECRET_KEY
    return hmac.new(
        str(secret).encode("utf-8"),
        _PURPOSE,
        hashlib.sha256,
    ).digest()


# ---------------------------------------------------------
# تولید / استخراج / بررسی توکن
# ---------------------------------------------------------

def new_nonce() -> str:
    """nonce تصادفی امن رمزنگاری برای یک سفارش."""
    return secrets.token_hex(TOKEN_BYTES)


def token_for_nonce(nonce: str) -> str:
    """توکن قطعی و امن برای یک nonce مشخص (قابل بازتولید، بدون ذخیرهٔ توکن خام)."""
    nonce = (nonce or "").strip()
    if not nonce:
        raise ValueError("tracking nonce is required")

    nonce_bytes = nonce.encode("ascii")
    tag = hmac.new(_tracking_key(), nonce_bytes, hashlib.sha256).digest()
    raw = nonce_bytes + tag
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def hash_token(token: str) -> str:
    """اثر انگشت توکن که در دیتابیس نگهداری می‌شود (توکن خام ذخیره نمی‌شود)."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def is_plausible_token(token) -> bool:
    """شکل ظاهری توکن — جلوی ورودی‌های عجیب/بسیار بزرگ را می‌گیرد."""
    if not isinstance(token, str):
        return False
    if not (40 <= len(token) <= 200):
        return False
    return all(c.isalnum() or c in "-_." for c in token)


def token_is_valid(order, token) -> bool:
    """مقایسهٔ امن (constant-time) توکن ارسالی با مقدار Hash شدهٔ همان سفارش."""
    stored = (getattr(order, "tracking_token_hash", "") or "")
    if not stored or not is_plausible_token(token):
        return False
    return hmac.compare_digest(hash_token(token), stored)


def current_token(order):
    """توکن فعلی سفارش (برای Retry/Idempotency) یا None اگر اعتبارنامه ندارد."""
    nonce = (getattr(order, "tracking_nonce", "") or "").strip()
    if not nonce:
        return None
    return token_for_nonce(nonce)


def issue_token(order, save=True):
    """
    صدور اعتبارنامهٔ تازه برای یک سفارش.

    nonce و hash توکن روی خود سفارش ذخیره می‌شوند؛ توکن خام برگردانده
    می‌شود و جای دیگری نگهداری نمی‌شود. صدور مجدد، توکن قبلی را باطل می‌کند.
    """
    nonce = new_nonce()
    token = token_for_nonce(nonce)

    order.tracking_nonce = nonce
    order.tracking_token_hash = hash_token(token)
    order.tracking_issued_at = timezone.now()

    if save:
        order.save(
            update_fields=[
                "tracking_nonce",
                "tracking_token_hash",
                "tracking_issued_at",
            ]
        )

    return token


def token_from_request(request):
    """
    خواندن توکن فقط از Header.

    توکن هرگز از Query String یا بدنهٔ URL خوانده نمی‌شود تا در لاگ‌ها،
    تاریخچهٔ مرورگر و Referer لو نرود.
    """
    raw = request.headers.get(TOKEN_HEADER) or ""

    if not raw:
        auth = request.headers.get("Authorization") or ""
        if auth[:7].lower() == "bearer ":
            raw = auth[7:]

    raw = raw.strip()
    return raw or None


# ---------------------------------------------------------
# اتصال به Session (بازیابی پس از Refresh، بدون LocalStorage)
# ---------------------------------------------------------

def bind_token_to_session(request, order_id, token):
    """
    ذخیرهٔ توکن در Session سمت سرور (کوکی HttpOnly) تا پیگیری پس از
    Refresh صفحه و باز شدن صفحهٔ مستقل سفارش بدون لو رفتن توکن کار کند.
    """
    if not token:
        return

    tokens = dict(request.session.get(SESSION_KEY) or {})
    tokens[str(order_id)] = token

    if len(tokens) > SESSION_MAX_ENTRIES:
        tokens = dict(list(tokens.items())[-SESSION_MAX_ENTRIES:])

    request.session[SESSION_KEY] = tokens


def session_token_for(request, order_id):
    tokens = request.session.get(SESSION_KEY) or {}
    return tokens.get(str(order_id))


def authorize_order_request(request, order, allow_session=True):
    """
    بررسی مالکیت سفارش برای درخواست مشتری.

    فقط با توکن معتبر همان سفارش (یا توکن همان سفارش در Session همین
    مرورگر) اجازه داده می‌شود. شناسهٔ سفارش به‌تنهایی هیچ ارزشی ندارد.

    Returns:
        (authorized: bool, token: str | None)
    """
    token = token_from_request(request)
    if token and token_is_valid(order, token):
        return True, token

    if allow_session:
        stored = session_token_for(request, order.id)
        if stored and token_is_valid(order, stored):
            return True, stored

    return False, None


def request_carries_customer_token(request) -> bool:
    """آیا درخواست یک توکن پیگیری مشتری همراه دارد؟ (برای مسدودکردن آن در APIهای مدیریتی)"""
    return token_from_request(request) is not None


# ---------------------------------------------------------
# Rate Limiting (Cache-based، بدون وابستگی اضافه)
# ---------------------------------------------------------

def rate_limited(scope: str, identifier: str, limit: int, window: int) -> bool:
    """
    پنجرهٔ ثابت ساده برای محدودکردن تلاش‌ها (حدس‌زدن توکن/شناسه).

    Args:
        scope: نام عملیات (کلید جداگانه برای هر سنجه)
        identifier: معمولاً IP کلاینت
        limit: حداکثر درخواست در هر پنجره
        window: طول پنجره به ثانیه
    """
    bucket = int(time.time()) // max(int(window), 1)
    key = f"order-sec:rl:{scope}:{identifier}:{bucket}"

    cache.add(key, 0, window + 5)
    try:
        count = cache.incr(key)
    except ValueError:
        # کلید بین add و incr منقضی شده بود
        cache.add(key, 1, window + 5)
        count = 1

    return count > limit
