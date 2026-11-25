"""
منبع مشترک خواندن تنظیمات میزها.

تعداد واقعی میزها فقط در ``RestaurantRobot.settings.FLOOR_TOTAL_TABLES``
تعریف میشود. هیچ ماژول دیگری نباید عدد ۶۴ را هاردکد کند؛ همه خوانندگان
باید از توابع این ماژول استفاده کنند.
"""

from django.conf import settings

FIRST_TABLE_NUMBER = 1


def get_floor_total_tables():
    """تعداد کل میزها را از تنظیمات پروژه میخواند."""
    return settings.FLOOR_TOTAL_TABLES


def get_table_numbers():
    """لیست شماره میزهای معتبر: ۱ تا ``FLOOR_TOTAL_TABLES``."""
    return list(
        range(FIRST_TABLE_NUMBER, get_floor_total_tables() + FIRST_TABLE_NUMBER)
    )


def get_max_table_number():
    """بزرگترین شمارهٔ میز معتبر."""
    return get_floor_total_tables()
