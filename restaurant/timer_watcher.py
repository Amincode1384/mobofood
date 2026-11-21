import logging
import threading
import time

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


def run_once():
    """
    یک دور بررسی زمان‌ها (بدون Thread — قابل تست).

    دو انتقال زمان‌محور:
      1. cooking → awaiting_confirmation  (پایان زمان آماده‌سازی)
      2. sent    → awaiting_confirmation  (پایان زمان سفر ربات)

    هیچ‌کدام سفارش را «تحویل‌شده» نمی‌کنند؛ `done` فقط با تأیید دریافت
    (مشتری یا آشپزخانه) ثبت می‌شود.
    """
    from django.utils import timezone

    from .events import broadcast_order_event
    from .models import Order
    from .views import serialize_order
    from . import order_statuses as order_status

    now = timezone.now()
    changed = 0

    transitions = [
        # (وضعیت فعلی، وضعیت مقصد)
        (order_status.STATUS_COOKING, order_status.STATUS_AWAITING_CONFIRMATION),
        (order_status.STATUS_SENT, order_status.STATUS_AWAITING_CONFIRMATION),
    ]

    for current, target in transitions:
        expired = Order.objects.filter(
            status=current,
            eta_at__isnull=False,
            eta_at__lte=now,
        )
        for order in expired:
            if not order_status.can_transition(order.status, target):
                continue

            order.status = target
            order.save(update_fields=["status"])
            broadcast_order_event("order_updated", serialize_order(order))
            logger.info(
                "Order #%s %s timer expired -> %s", order.id, current, target
            )
            changed += 1

    return changed


def run_forever():
    """Blocking loop for a dedicated process (management command / container).

    Under Daphne, ``apps.ready()`` does not start the in-process thread, so
    production must run this loop as its own process.
    """
    while True:
        try:
            run_once()
        except Exception:
            # Never let a transient DB hiccup kill the watcher process.
            logger.exception("Timer watcher iteration failed")
        time.sleep(CHECK_INTERVAL_SECONDS)


def _run_loop():
    # Imports are deferred until the thread actually runs, so Django's
    # app registry is guaranteed to be ready.
    run_forever()
