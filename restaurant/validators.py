"""
اعتبارسنجی‌های امنیتی برای سیستم سفارش

این ماژول شامل اعتبارسنجی‌های server-side برای:
1. شماره تلفن مشتری
2. شماره میز و امضای امنیتی آن (QR Code)

قالب‌های معتبر شماره تلفن:
- 09123456789
- +989123456789
- 00989123456789
- 989123456789
"""

import re
from django.core.exceptions import ValidationError
from .qr_generator import verify_table_signature


def validate_iranian_phone(phone):
    """
    اعتبارسنجی شماره موبایل ایران

    Args:
        phone (str): شماره تلفن ورودی

    Returns:
        str: شماره نرمال‌شده در قالب 09XXXXXXXXX

    Raises:
        ValidationError: در صورت نامعتبر بودن شماره
    """

    if not phone:
        raise ValidationError("شماره تماس نمی‌تواند خالی باشد.")

    # حذف فاصله، خط تیره و کاراکترهای جداکننده
    phone = phone.strip()
    phone = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

    # بررسی وجود کاراکتر غیرمجاز (فقط + در ابتدا مجاز است)
    if not re.match(r'^[\+]?[0-9]+$', phone):
        raise ValidationError(
            "شماره تماس فقط می‌تواند شامل اعداد باشد. "
            "کاراکترهای غیرمجاز استفاده شده است."
        )

    # نرمال‌سازی قالب‌های مختلف
    # +989123456789 → 989123456789
    if phone.startswith('+98'):
        phone = phone[1:]  # حذف +

    # 00989123456789 → 989123456789
    elif phone.startswith('0098'):
        phone = phone[2:]  # حذف 00

    # 989123456789 → 09123456789
    if phone.startswith('98') and len(phone) == 12:
        phone = '0' + phone[2:]

    # بررسی قالب نهایی: 09XXXXXXXXX
    if not re.match(r'^09[0-9]{9}$', phone):
        raise ValidationError(
            "قالب شماره تماس نامعتبر است. "
            "شماره باید با ۰۹ شروع شود و ۱۱ رقم باشد. "
            "مثال: 09123456789"
        )

    # بررسی اعتبار پیش‌شماره اپراتور
    # اپراتورهای معتبر ایران
    valid_prefixes = [
        '0910', '0911', '0912', '0913', '0914', '0915', '0916', '0917', '0918', '0919',  # همراه اول
        '0901', '0902', '0903', '0905', '0930', '0933', '0935', '0936', '0937', '0938', '0939',  # ایرانسل
        '0920', '0921',  # رایتل
        '0932', '0934',  # تله‌کیش (کیش‌سل سابق)
        '0990', '0991', '0992', '0993', '0994', '0998', '0999',  # MVNO و اپراتورهای مجازی
    ]

    prefix = phone[:4]
    if prefix not in valid_prefixes:
        raise ValidationError(
            f"پیش‌شماره {prefix} معتبر نیست. "
            "لطفاً شماره موبایل ایرانی معتبر وارد کنید."
        )

    return phone


def validate_optional_phone(phone):
    """
    اعتبارسنجی شماره تلفن اختیاری

    اگر شماره خالی باشد، None برمی‌گرداند.
    اگر شماره وارد شده باشد، اعتبارسنجی می‌کند.

    Args:
        phone (str): شماره تلفن ورودی

    Returns:
        str or None: شماره نرمال‌شده یا None

    Raises:
        ValidationError: در صورت نامعتبر بودن شماره
    """

    if not phone or not phone.strip():
        return None

    return validate_iranian_phone(phone)


def validate_table_number(table_number, signature=None):
    """
    اعتبارسنجی server-side شماره میز (حیاتی برای امنیت)

    این تابع اعتبارسنجی‌های زیر را انجام می‌دهد:
    1. بررسی نوع داده (باید عدد صحیح باشد)
    2. بررسی محدوده معتبر (1-64)
    3. بررسی امضای امنیتی QR Code (اگر ارائه شده باشد)

    Args:
        table_number: شماره میز (می‌تواند string یا int باشد)
        signature (str, optional): امضای امنیتی QR Code

    Returns:
        int: شماره میز معتبر

    Raises:
        ValidationError: در صورت نامعتبر بودن شماره میز یا امضا

    Security Notes:
        - این تابع MUST در تمام API endpoints ای که شماره میز دریافت می‌کنند
          فراخوانی شود (به خصوص create_order)
        - امضای QR Code را همیشه بررسی کنید تا از حملات replay جلوگیری شود
        - لاگ تلاش‌های ناموفق برای شناسایی حملات
    """

    # ۱. بررسی نوع داده و تبدیل
    if table_number is None:
        raise ValidationError(
            "شماره میز مشخص نشده است.",
            code="table_required"
        )

    # تبدیل به int (اگر string باشد)
    try:
        table_number = int(table_number)
    except (ValueError, TypeError):
        raise ValidationError(
            "شماره میز نامعتبر است. باید یک عدد صحیح باشد.",
            code="table_invalid_type"
        )

    # ۲. بررسی محدوده معتبر (1-64)
    if not (1 <= table_number <= 64):
        raise ValidationError(
            f"شماره میز {table_number} خارج از محدوده مجاز است. "
            f"میز باید بین 1 تا 64 باشد.",
            code="table_out_of_range"
        )

    # ۳. بررسی امضای امنیتی (اگر ارائه شده باشد)
    if signature is not None:
        if not isinstance(signature, str) or not signature.strip():
            raise ValidationError(
                "امضای امنیتی نامعتبر است.",
                code="signature_invalid"
            )

        if not verify_table_signature(table_number, signature):
            # این یک تلاش برای دستکاری است - باید لاگ شود
            raise ValidationError(
                "امضای امنیتی QR Code نامعتبر است. "
                "لطفاً QR Code روی میز خود را دوباره اسکن کنید.",
                code="signature_mismatch"
            )

    return table_number


def validate_table_with_signature(table_number, signature):
    """
    اعتبارسنجی شماره میز همراه با الزام وجود امضای معتبر

    این تابع سخت‌گیرانه‌تر از validate_table_number است
    و حتماً امضای معتبر می‌خواهد.

    Args:
        table_number: شماره میز
        signature (str): امضای امنیتی QR Code (الزامی)

    Returns:
        int: شماره میز معتبر

    Raises:
        ValidationError: در صورت نامعتبر بودن یا نبود امضا

    Use Case:
        برای endpoint هایی که حتماً باید از QR Code اسکن شده باشند
        (مثل ثبت سفارش نهایی)
    """

    if signature is None or not signature.strip():
        raise ValidationError(
            "امضای امنیتی الزامی است. لطفاً QR Code روی میز را اسکن کنید.",
            code="signature_required"
        )

    return validate_table_number(table_number, signature)
