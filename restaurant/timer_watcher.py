import threading
import time
import logging

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()

CHECK_INTERVAL_SECONDS = 3


def start():
    """Idempotent — safe to call multiple times, only spins up one thread."""
    global _started
    with _lock:
        if _started:
            return
        _started = True

    thread = threading.Thread(target=_run_loop, name="timer-watcher", daemon=True)
    thread.start()
    logger.info("Timer watcher thread started.")


def _run_loop():
    # Imports are deferred until the thread actually runs, so Django's
    # app registry is guaranteed to be ready.
    from django.utils import timezone
    from .models import Order
    from .events import broadcast_order_event
    from .views import serialize_order

    while True:
        try:
            expired = Order.objects.filter(
                status="cooking",
                eta_at__isnull=False,
                eta_at__lte=timezone.now(),
            )
            for order in expired:
                order.status = "awaiting_confirmation"
                order.save(update_fields=["status"])
                broadcast_order_event("order_updated", serialize_order(order))
                logger.info("Order #%s timer expired -> awaiting_confirmation", order.id)
        except Exception:
            # Never let a transient DB hiccup kill the watcher thread.
            logger.exception("Timer watcher iteration failed")

        time.sleep(CHECK_INTERVAL_SECONDS)