"""Run the order-timer watcher as a dedicated foreground process.

``restaurant.apps.RestaurantConfig.ready()`` only starts the background
thread under ``manage.py runserver``. Under Daphne (production) this command
must run alongside the web process, otherwise orders never leave the
``cooking`` / ``sent`` states on time.
"""

from django.core.management.base import BaseCommand

from restaurant import timer_watcher


class Command(BaseCommand):
    help = "Expire cooking/robot-travel timers in a dedicated process."

    def handle(self, *args, **options):
        self.stdout.write("Timer watcher started (interval: %ss)."
                          % timer_watcher.CHECK_INTERVAL_SECONDS)
        try:
            timer_watcher.run_forever()
        except KeyboardInterrupt:
            self.stdout.write("Timer watcher stopped.")
