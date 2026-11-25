"""
تستهای منبع واحد تعداد میزها (FLOOR_TOTAL_TABLES).

تضمین میکند تعداد میزها در کل پروژه از تنظیمات خوانده میشود، لیست میزها
1 تا FLOOR_TOTAL_TABLES است و اعتبارسنجی شماره میز با همان منبع هماهنگ است.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from restaurant import mission_statuses
from restaurant.movement_command import (
    InvalidDestinationError,
    normalize_table_number,
    to_destination_id,
)
from restaurant.table_config import (
    FIRST_TABLE_NUMBER,
    get_floor_total_tables,
    get_max_table_number,
    get_table_numbers,
)
from restaurant.validators import validate_table_number


class FloorTotalTablesTests(SimpleTestCase):

    def test_floor_total_tables_is_64(self):
        self.assertEqual(settings.FLOOR_TOTAL_TABLES, 64)
        self.assertEqual(get_floor_total_tables(), 64)

    def test_generated_table_numbers_match_setting(self):
        expected = list(
            range(FIRST_TABLE_NUMBER, settings.FLOOR_TOTAL_TABLES + FIRST_TABLE_NUMBER)
        )
        self.assertEqual(get_table_numbers(), expected)
        self.assertEqual(len(get_table_numbers()), settings.FLOOR_TOTAL_TABLES)
        self.assertEqual(get_table_numbers()[0], 1)
        self.assertEqual(get_table_numbers()[-1], 64)

    def test_max_table_number_comes_from_setting(self):
        self.assertEqual(get_max_table_number(), settings.FLOOR_TOTAL_TABLES)
        self.assertEqual(mission_statuses.MAX_TABLE_NUMBER, settings.FLOOR_TOTAL_TABLES)

    def test_validators_read_same_setting(self):
        self.assertEqual(validate_table_number(1), 1)
        self.assertEqual(validate_table_number(64), 64)
        self.assertEqual(normalize_table_number(64), 64)
        self.assertEqual(to_destination_id(64), "TABLE_64")

    def test_out_of_range_and_non_numeric_tables_rejected(self):
        for invalid in (0, 65, -1, "abc", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_table_number(invalid)
                with self.assertRaises(InvalidDestinationError):
                    normalize_table_number(invalid)

    def test_customer_page_exposes_table_numbers_from_setting(self):
        from django.urls import reverse

        response = self.client.get(reverse("customer"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["table_numbers"], get_table_numbers()
        )
        self.assertEqual(
            response.context["floor_total_tables"], settings.FLOOR_TOTAL_TABLES
        )
