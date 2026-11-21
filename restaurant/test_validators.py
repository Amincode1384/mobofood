"""
تست‌های اعتبارسنجی شماره تلفن
"""

from django.test import TestCase
from django.core.exceptions import ValidationError
from .validators import validate_iranian_phone, validate_optional_phone


class IranianPhoneValidatorTestCase(TestCase):
    """
    تست‌های اعتبارسنجی شماره موبایل ایران
    """

    def test_valid_phone_09_format(self):
        """تست شماره معتبر با قالب 09XXXXXXXXX"""
        phone = "09123456789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_phone_with_98_prefix(self):
        """تست شماره معتبر با پیش‌شماره 98"""
        phone = "989123456789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_phone_with_plus_98(self):
        """تست شماره معتبر با +98"""
        phone = "+989123456789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_phone_with_0098(self):
        """تست شماره معتبر با 0098"""
        phone = "00989123456789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_phone_with_spaces(self):
        """تست شماره معتبر با فاصله"""
        phone = "0912 345 6789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_phone_with_dashes(self):
        """تست شماره معتبر با خط تیره"""
        phone = "0912-345-6789"
        result = validate_iranian_phone(phone)
        self.assertEqual(result, "09123456789")

    def test_valid_operators_hamrahe_aval(self):
        """تست شماره‌های همراه اول"""
        valid_prefixes = ['0910', '0911', '0912', '0913', '0914', '0915', '0916', '0917', '0918', '0919']
        for prefix in valid_prefixes:
            phone = f"{prefix}1234567"
            result = validate_iranian_phone(phone)
            self.assertEqual(result, phone)

    def test_valid_operators_irancell(self):
        """تست شماره‌های ایرانسل"""
        valid_prefixes = ['0901', '0902', '0903', '0905', '0930', '0933', '0935', '0936', '0937', '0938', '0939']
        for prefix in valid_prefixes:
            phone = f"{prefix}1234567"
            result = validate_iranian_phone(phone)
            self.assertEqual(result, phone)

    def test_valid_operators_rightel(self):
        """تست شماره‌های رایتل"""
        valid_prefixes = ['0920', '0921']
        for prefix in valid_prefixes:
            phone = f"{prefix}1234567"
            result = validate_iranian_phone(phone)
            self.assertEqual(result, phone)

    def test_empty_phone(self):
        """تست شماره خالی"""
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone("")
        self.assertIn("خالی", str(context.exception))

    def test_phone_with_letters(self):
        """تست شماره حاوی حروف"""
        phone = "0912abc6789"
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        self.assertIn("غیرمجاز", str(context.exception))

    def test_phone_too_short(self):
        """تست شماره کوتاه‌تر از حد مجاز"""
        phone = "091234567"  # 9 رقم به‌جای 11
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        self.assertIn("نامعتبر", str(context.exception))

    def test_phone_too_long(self):
        """تست شماره بلندتر از حد مجاز"""
        phone = "091234567890"  # 12 رقم به‌جای 11
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        self.assertIn("نامعتبر", str(context.exception))

    def test_invalid_prefix(self):
        """تست شماره با پیش‌شماره نامعتبر"""
        phone = "08001234567"  # پیش‌شماره 0800 معتبر نیست
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        # بررسی که خطا رخ داده است (پیام می‌تواند متفاوت باشد)
        self.assertTrue(str(context.exception))

    def test_phone_not_starting_with_09(self):
        """تست شماره که با 09 شروع نمی‌شود"""
        phone = "02112345678"  # شماره تلفن ثابت
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        # بررسی که خطا رخ داده است
        self.assertTrue(str(context.exception))

    def test_phone_with_special_characters(self):
        """تست شماره با کاراکترهای خاص"""
        phone = "0912#345@6789"
        with self.assertRaises(ValidationError) as context:
            validate_iranian_phone(phone)
        self.assertIn("غیرمجاز", str(context.exception))


class OptionalPhoneValidatorTestCase(TestCase):
    """
    تست‌های اعتبارسنجی شماره تلفن اختیاری
    """

    def test_empty_phone_returns_none(self):
        """تست شماره خالی باید None برگرداند"""
        result = validate_optional_phone("")
        self.assertIsNone(result)

    def test_whitespace_phone_returns_none(self):
        """تست شماره فقط با فاصله باید None برگرداند"""
        result = validate_optional_phone("   ")
        self.assertIsNone(result)

    def test_none_phone_returns_none(self):
        """تست مقدار None باید None برگرداند"""
        result = validate_optional_phone(None)
        self.assertIsNone(result)

    def test_valid_phone_returns_normalized(self):
        """تست شماره معتبر باید نرمال‌شده برگردد"""
        result = validate_optional_phone("0912 345 6789")
        self.assertEqual(result, "09123456789")

    def test_invalid_phone_raises_error(self):
        """تست شماره نامعتبر باید خطا بدهد"""
        with self.assertRaises(ValidationError):
            validate_optional_phone("invalid")
