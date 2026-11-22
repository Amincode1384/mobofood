# -*- coding: utf-8 -*-
"""
اتصال Django logging و به‌استثنای‌های پردازش‌نشده به SystemErrorLog.

دو جزء:

  * ``SystemErrorReportingMiddleware`` — ثبت استثناهای پردازش‌نشدهٔ view
    (process_exception) و پاسخ‌های HTTP 5xx (process_response).
  * ``SystemErrorDatabaseHandler`` — handler لاگینگ که رکوردهای WARNING+
    لاگر ``restaurant`` را در SystemErrorLog می‌نشاند.

این ماژول عمداً در سطح ماژول فقط ``logging`` و Django را import می‌کند؛
ورودی به ``error_logs`` (که مدل دارد) داخل توابع و به‌صورت lazy انجام
می‌شود تا هنگام راه‌اندازی (پیش از آماده‌شدن app registry) امن باشد.

هر دو جزء کاملاً fail-open هستند: خطای داخلی‌شان هرگز به درخواست اصلی
نفوذ نمی‌کند.
"""

import logging
import threading

from django.http import Http404
from django.core.exceptions import PermissionDenied, SuspiciousOperation
from django.utils.deprecation import MiddlewareMixin

logger = logging.getLogger(__name__)

# استثناهایی که بخشی عادی از جریان HTTP هستند و خطا محسوب نمی‌شوند.
_BENIGN_EXCEPTIONS = (Http404, PermissionDenied, SuspiciousOperation)

_LEVEL_BY_LOG_LEVEL = (
    (logging.CRITICAL, "CRITICAL"),
    (logging.ERROR, "ERROR"),
    (logging.WARNING, "WARNING"),
    (logging.INFO, "INFO"),
)


def source_for_path(path):
    """طبقه‌بندی لایه (order/mission/api/view) بر اساس مسیر درخواست."""
    p = path or ""
    if p.startswith("/api/missions"):
        return "mission"
    if p.startswith("/api/create_order") or p.startswith("/api/orders"):
        return "order"
    if p.startswith("/api/"):
        return "api"
    return "view"


def _safe_record(**kwargs):
    """فراخوانی امن record_system_error؛ هرگز exception پس نمی‌دهد."""
    try:
        from .error_logs import record_system_error
        return record_system_error(**kwargs)
    except Exception:
        # فقط یک بار ساکت شکست می‌خورد تا حلقهٔ لاگ به وجود نیاید.
        logger.debug("system error reporting unavailable", exc_info=True)
        return False


class SystemErrorReportingMiddleware(MiddlewareMixin):
    """
    ثبت خطاهای مهم در سطح HTTP:

    - ``process_exception``: استثنای پردازش‌نشدهٔ view (پاسخ 500 واقعی)
      با ``code=unhandled_exception`` و source بر اساس مسیر.
    - ``process_response``: هر پاسخ 5xx که view خودش برگردانده (مثل
      JsonResponse با status=500) با ``code=http_500_response``.
    """

    def process_exception(self, request, exception):
        if isinstance(exception, _BENIGN_EXCEPTIONS):
            return None

        try:
            path = getattr(request, "path", "") or ""
        except Exception:
            path = ""

        _safe_record(
            level="ERROR",
            source=source_for_path(path),
            code="unhandled_exception",
            message=f"{type(exception).__name__}: {exception}",
            path=path,
            exc_info=(type(exception), exception, exception.__traceback__),
        )
        # جلوگیری از ثبت دوباره در process_response با کد دیگر.
        request._system_error_recorded = True
        return None

    def process_response(self, request, response):
        try:
            if response.status_code >= 500 and not getattr(
                request, "_system_error_recorded", False
            ):
                path = getattr(request, "path", "") or ""
                method = getattr(request, "method", "") or ""
                _safe_record(
                    level="CRITICAL" if response.status_code >= 500 else "ERROR",
                    source=source_for_path(path),
                    code="http_500_response",
                    message=f"HTTP {response.status_code} در پاسخ به {method} {path}",
                    path=path,
                )
                request._system_error_recorded = True
        except Exception:
            logger.debug("system error middleware failed", exc_info=True)
        return response


class SystemErrorDatabaseHandler(logging.Handler):
    """
    handler متمرکز: رکورد لاگینگ (سطح WARNING به بالا) را با همان
    قواعد (sanitize + deduplicate + fail-open) در SystemErrorLog می‌نشاند.

    فیلدهای اختیاری روی record پشتیبانی می‌شوند: ``source``، ``code``،
    ``path``، ``order_uid``، ``mission_uid``، ``robot_id``.
    """

    # گارد re-entrancy اختصاصی (در کنار گارد داخل record_system_error).
    _local = threading.local()

    def emit(self, record):
        if getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            level = "INFO"
            for threshold, name in _LEVEL_BY_LOG_LEVEL:
                if record.levelno >= threshold:
                    level = name
                    break

            message = record.getMessage()
            if record.exc_info:
                etype, evalue, _tb = record.exc_info
                summary = f"{getattr(etype, '__name__', etype)}: {evalue}"
                if summary not in message:
                    message = f"{message} | {summary}"

            _safe_record(
                level=level,
                source=getattr(record, "source", None) or "other",
                code=getattr(record, "code", None)
                or f"log_{record.levelname.lower()}",
                message=message,
                path=getattr(record, "path", None) or "",
                order_uid=getattr(record, "order_uid", None) or "",
                mission_uid=getattr(record, "mission_uid", None) or "",
                robot_id=getattr(record, "robot_id", None) or "",
            )
        except Exception:
            # fail-open: handler لاگینگ هرگز نباید exception پس بدهد.
            pass
        finally:
            self._local.active = False
