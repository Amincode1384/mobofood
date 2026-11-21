"""
ماژول تولید QR Code برای میزهای رستوران

این ماژول برای تولید QR Code های امن و منحصر به فرد برای هر میز استفاده می‌شود.
هر QR Code شامل یک توکن امنیتی است که از دستکاری جلوگیری می‌کند.
"""

import qrcode
import os
import hashlib
import hmac
import json
from io import BytesIO
from django.conf import settings
from django.core.cache import cache


# کلید مخفی برای امضای QR Code ها.
# اگر QR_SECRET_KEY در settings تعریف نشده باشد، از SECRET_KEY خود Django
# (که باید در production از متغیر محیطی بیاید) استفاده می‌شود تا امضای
# میز به‌سادگی قابل جعل نباشد.
SECRET_KEY = (
    os.environ.get('QR_SECRET_KEY')
    or getattr(settings, 'QR_SECRET_KEY', None)
    or settings.SECRET_KEY
)


def generate_table_signature(table_number):
    """
    تولید امضای امنیتی برای شماره میز

    این امضا جلوی دستکاری شماره میز را می‌گیرد.

    Args:
        table_number (int): شماره میز (1-64)

    Returns:
        str: امضای SHA-256 به صورت hex
    """
    data = f"{table_number}:{SECRET_KEY}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def verify_table_signature(table_number, signature):
    """
    بررسی صحت امضای شماره میز

    Args:
        table_number (int): شماره میز
        signature (str): امضای دریافت شده

    Returns:
        bool: True اگر امضا معتبر باشد
    """
    expected_signature = generate_table_signature(table_number)
    # مقایسهٔ امن در برابر timing attack
    return hmac.compare_digest(str(signature), str(expected_signature))


def generate_table_qr_url(table_number, base_url="http://localhost:8000"):
    """
    تولید URL برای QR Code میز

    Args:
        table_number (int): شماره میز (1-64)
        base_url (str): آدرس پایه سایت

    Returns:
        str: URL کامل با پارامترهای امنیتی
    """
    if not (1 <= table_number <= 64):
        raise ValueError(f"شماره میز باید بین 1 تا 64 باشد. دریافت شده: {table_number}")

    signature = generate_table_signature(table_number)
    url = f"{base_url}/?table={table_number}&sig={signature}"
    return url


def generate_qr_code_image(table_number, base_url="http://localhost:8000"):
    """
    تولید تصویر QR Code برای یک میز

    Args:
        table_number (int): شماره میز (1-64)
        base_url (str): آدرس پایه سایت

    Returns:
        BytesIO: تصویر PNG در حافظه
    """
    url = generate_table_qr_url(table_number, base_url)

    # تنظیمات QR Code
    qr = qrcode.QRCode(
        version=1,  # سایز خودکار
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # بالاترین سطح اصلاح خطا
        box_size=10,  # سایز هر مربع
        border=4,  # حاشیه (minimum is 4)
    )

    qr.add_data(url)
    qr.make(fit=True)

    # تولید تصویر
    img = qr.make_image(fill_color="black", back_color="white")

    # ذخیره در حافظه
    buffer = BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)

    return buffer


def generate_all_qr_codes(base_url="http://localhost:8000"):
    """
    تولید QR Code برای تمام 64 میز

    Args:
        base_url (str): آدرس پایه سایت

    Returns:
        dict: دیکشنری با شماره میز و buffer تصویر
    """
    qr_codes = {}

    for table_num in range(1, 65):
        qr_codes[table_num] = {
            'image': generate_qr_code_image(table_num, base_url),
            'url': generate_table_qr_url(table_num, base_url),
            'signature': generate_table_signature(table_num)
        }

    return qr_codes


def cache_table_access(table_number, duration_seconds=3600):
    """
    کش کردن دسترسی به میز برای جلوگیری از اسکن مکرر

    Args:
        table_number (int): شماره میز
        duration_seconds (int): مدت زمان اعتبار کش (پیش‌فرض 1 ساعت)

    Returns:
        str: کلید کش
    """
    cache_key = f"table_access_{table_number}"
    cache.set(cache_key, True, duration_seconds)
    return cache_key


def is_table_recently_accessed(table_number):
    """
    بررسی اینکه آیا میز اخیراً اسکن شده است

    Args:
        table_number (int): شماره میز

    Returns:
        bool: True اگر میز در کش باشد
    """
    cache_key = f"table_access_{table_number}"
    return cache.get(cache_key) is not None


def get_table_info(table_number):
    """
    اطلاعات کامل یک میز

    Args:
        table_number (int): شماره میز

    Returns:
        dict: اطلاعات میز
    """
    return {
        'table_number': table_number,
        'signature': generate_table_signature(table_number),
        'qr_url': generate_table_qr_url(table_number),
        'is_valid': 1 <= table_number <= 64,
        'recently_accessed': is_table_recently_accessed(table_number)
    }
