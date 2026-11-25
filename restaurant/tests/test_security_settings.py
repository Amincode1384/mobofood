"""
تستهای تنظیمات امنیتی (SECRET_KEY / DEBUG / ALLOWED_HOSTS).

تضمین میکند رازها هاردکد نشدهاند، DEBUG بهطور پیشفرض fail-secure است و
در production صریح، نبود کلید یا ALLOWED_HOSTS='*' منجر به fail-fast میشود.
"""

import importlib
import os
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

SETTINGS_MODULE = "RestaurantRobot.settings"

# رشتههایی که نباید بهعنوان کلید پیشفرض در کد/settings ظاهر شوند.
KNOWN_HARDCODED_KEYS = (
    "django-insecure-bze2os122$95nfei1d9gj-!8ooh@3!zxlb!gq37w$kutmc5sg^",
)


def _load_settings(env):
    """بارگذاری مجدد settings با محیط دلخواه و بازگرداندن مقدارها."""
    with patch.dict(os.environ, env, clear=True):
        module = importlib.reload(importlib.import_module(SETTINGS_MODULE))
        values = {
            "DEBUG": module.DEBUG,
            "PRODUCTION": module.PRODUCTION,
            "SECRET_KEY": module.SECRET_KEY,
            "ALLOWED_HOSTS": module.ALLOWED_HOSTS,
        }
    # بازگرداندن تنظیمات واقعی فرآیند تست، چون reload ماژول را تغییر داد.
    importlib.reload(importlib.import_module(SETTINGS_MODULE))
    from django.conf import settings as django_settings

    django_settings._wrapped = django_settings.__class__(SETTINGS_MODULE)
    return values


class SecuritySettingsTests(SimpleTestCase):

    def test_secret_key_is_read_from_environment(self):
        values = _load_settings(
            {"DJANGO_SECRET_KEY": "env-provided-key", "DEBUG": "1"}
        )
        self.assertEqual(values["SECRET_KEY"], "env-provided-key")
        self.assertNotIn(values["SECRET_KEY"], KNOWN_HARDCODED_KEYS)

    def test_secret_key_fallback_name_is_supported(self):
        values = _load_settings({"SECRET_KEY": "legacy-env-key", "DEBUG": "1"})
        self.assertEqual(values["SECRET_KEY"], "legacy-env-key")

    def test_debug_defaults_to_false_and_allows_localhost(self):
        values = _load_settings({})
        self.assertFalse(values["DEBUG"])
        self.assertEqual(values["ALLOWED_HOSTS"], ["localhost", "127.0.0.1"])

    def test_debug_true_is_opt_in(self):
        values = _load_settings({"DJANGO_DEBUG": "1"})
        self.assertTrue(values["DEBUG"])
        self.assertFalse(values["PRODUCTION"])

    def test_production_requires_secret_key(self):
        with self.assertRaises(ImproperlyConfigured):
            _load_settings({"DEBUG": "0"})

    def test_production_rejects_wildcard_allowed_hosts(self):
        with self.assertRaises(ImproperlyConfigured):
            _load_settings(
                {
                    "DEBUG": "0",
                    "DJANGO_SECRET_KEY": "env-only-test-key",
                    "DJANGO_ALLOWED_HOSTS": "*",
                }
            )

    def test_production_accepts_explicit_hosts(self):
        values = _load_settings(
            {
                "DEBUG": "0",
                "DJANGO_SECRET_KEY": "env-only-test-key",
                "DJANGO_ALLOWED_HOSTS": "restaurant.example.com,10.0.0.10",
            }
        )
        self.assertTrue(values["PRODUCTION"])
        self.assertEqual(
            values["ALLOWED_HOSTS"],
            ["restaurant.example.com", "10.0.0.10"],
        )

    def test_no_hardcoded_secret_in_settings_source(self):
        from pathlib import Path

        source = (Path(settings.BASE_DIR) / "RestaurantRobot" / "settings.py").read_text(
            encoding="utf-8"
        )
        for key in KNOWN_HARDCODED_KEYS:
            self.assertNotIn(key, source)
