import os
import sys

from django.apps import AppConfig


class RestaurantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "restaurant"

    def ready(self):
        if "runserver" not in sys.argv:
            return

        if os.environ.get("RUN_MAIN") != "true":
            return

        from . import timer_watcher
        timer_watcher.start()