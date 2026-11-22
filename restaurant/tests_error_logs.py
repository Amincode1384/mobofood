# -*- coding: utf-8 -*-
"""
تست‌های قابلیت مشاهده‌پذیری خطاهای سیستم (SystemError Reporting).

پوشش (معیار مأموریت: حداقل ۶ تست):

 1. ثبت خطا با فیلدهای اجباری (level/source/message/created_at)
 2. هر چهار سطح + اصلاح خودکار سطح/منبع نامعتبر
 3. deduplicate در بازهٔ ۶۰ ثانیه (occurrences) + جداسازی identifier
 4. پاک‌سازی داده حساس (تلفن، token، SECRET_KEY) قبل از ذخیره
 5. fail-open هنگام خطای دیتابیس (بدون exception، بازگشت به logging)
 6. middleware: استثنای پردازش‌نشده و پاسخ 5xx (پوشش API/View/Order)
 7. پوشش لایهٔ مأموریت: شکست transport در dispatch_mission
 8. پوشش لایهٔ WebSocket: خطای consumer ربات
 9. داشبورد: فقط is_staff + فیلتر level/source/mission_uid + بدون نشت PII
10. handler لاگینگ: رکورد WARNING از لاگر restaurant در SystemErrorLog
"""

import logging
import uuid
from datetime import timedelta
from unittest import mock

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import OperationalError
from django.http import Http404, JsonResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import path
from django.utils import timezone

from RestaurantRobot.asgi import application

from .error_logs import (
    DEDUP_WINDOW_SECONDS,
    LEVEL_CHOICES,
    SystemErrorLog,
    record_system_error,
)
from .error_logging import SystemErrorReportingMiddleware, source_for_path
from .mission_dispatch import DispatchError, dispatch_mission
from .mission_statuses import STATUS_PENDING
from .models import Mission, Order
from .robot_transport import RobotTimeoutError

TOKEN = "ws-token-123456"
ROBOT_ID = "RBOT-09"


# ------------------------------------------------------------------
# view آزمایشی برای تست سرتاسری middleware (فقط داخل این تست‌ها)
# ------------------------------------------------------------------
def _boom_view(request):
    raise RuntimeError("view failure for middleware test")


urlpatterns = [
    path("_boom/", _boom_view, name="_boom_test"),
]


# ==================================================================
# 1) تابع مرکزی record_system_error
# ==================================================================
class RecordSystemErrorTests(TestCase):

    def test_record_creates_row_with_required_fields(self):
        ok = record_system_error(
            level="ERROR",
            source="order",
            code="create_order_failed",
            message="خطا در ثبت سفارش",
            order_uid="12",
            path="/api/create_order/",
        )

        self.assertTrue(ok)
        row = SystemErrorLog.objects.get()
        self.assertEqual(row.level, "ERROR")
        self.assertEqual(row.source, "order")
        self.assertEqual(row.code, "create_order_failed")
        self.assertEqual(row.message, "خطا در ثبت سفارش")
        self.assertEqual(row.order_uid, "12")
        self.assertEqual(row.path, "/api/create_order/")
        self.assertIsNotNone(row.created_at)
        self.assertEqual(row.occurrences, 1)

    def test_all_levels_supported_and_invalid_values_coerced(self):
        for level, _label in LEVEL_CHOICES:
            record_system_error(
                level=level, source="api", code=f"code_{level}", message="m"
            )

        # سطح و منبع نامعتبر باید به پیش‌فرض امن اصلاح شوند.
        record_system_error(
            level="BOGUS_LEVEL", source="nope", code="invalid_choices", message="m"
        )

        self.assertEqual(
            set(
                SystemErrorLog.objects.filter(code__startswith="code_")
                .values_list("level", flat=True)
            ),
            {value for value, _label in LEVEL_CHOICES},
        )
        row = SystemErrorLog.objects.get(code="invalid_choices")
        self.assertEqual(row.level, "ERROR")
        self.assertEqual(row.source, "other")

    def test_duplicates_within_window_are_deduplicated(self):
        mission_uid = str(uuid.uuid4())
        for _ in range(3):
            record_system_error(
                level="ERROR",
                source="mission",
                code="robot_timeout",
                message="timeout",
                mission_uid=mission_uid,
                path="restaurant.mission_dispatch",
            )

        # یک ردیف با شمارش تکرار (لاگ انبوه نمی‌شود)
        self.assertEqual(SystemErrorLog.objects.count(), 1)
        row = SystemErrorLog.objects.get()
        self.assertEqual(row.occurrences, 3)
        self.assertIsNotNone(row.last_seen_at)

        # کد متفاوت ⇒ ردیف جدید
        record_system_error(
            level="ERROR", source="mission", code="robot_rejected",
            message="rejected", mission_uid=mission_uid,
        )
        self.assertEqual(SystemErrorLog.objects.count(), 2)

        # identifier متفاوت ⇒ ردیف جدید
        record_system_error(
            level="ERROR", source="mission", code="robot_timeout",
            message="timeout", mission_uid=str(uuid.uuid4()),
        )
        self.assertEqual(SystemErrorLog.objects.count(), 3)

        # بیرون از بازهٔ ۶۰ ثانیه ⇒ ردیف جدید
        SystemErrorLog.objects.filter(
            code="robot_timeout", mission_uid=mission_uid
        ).update(
            created_at=timezone.now()
            - timedelta(seconds=DEDUP_WINDOW_SECONDS + 5)
        )
        record_system_error(
            level="ERROR", source="mission", code="robot_timeout",
            message="timeout", mission_uid=mission_uid,
        )
        self.assertEqual(SystemErrorLog.objects.count(), 4)

    def test_sensitive_data_is_sanitized_before_storage(self):
        phone = "09123456789"
        robot_token = "super-secret-token-abc123"
        from django.conf import settings

        with self.settings(ROBOT_API_TOKEN=robot_token):
            record_system_error(
                level="ERROR",
                source="api",
                code="leak_attempt",
                message=(
                    f"کاربر {phone} با token={robot_token} ناموفق بود "
                    f"و SECRET_KEY={settings.SECRET_KEY} هم نباید ذخیره شود"
                ),
            )

        row = SystemErrorLog.objects.get(code="leak_attempt")
        self.assertNotIn(phone, row.message)
        self.assertNotIn(robot_token, row.message)
        self.assertNotIn(settings.SECRET_KEY, row.message)
        self.assertIn("[PHONE]", row.message)
        self.assertIn("[REDACTED]", row.message)

    def test_fail_open_when_database_unavailable(self):
        with mock.patch.object(
            SystemErrorLog,
            "objects",
        ) as objects_mock:
            objects_mock.filter.side_effect = OperationalError(
                "unable to open database file"
            )
            with self.assertLogs("restaurant.error_logs", level="ERROR") as logs:
                ok = record_system_error(
                    level="CRITICAL",
                    source="api",
                    code="db_unavailable",
                    message="x",
                )

        # ثبت خطا هرگز exception پس نمی‌دهد و به logging استاندارد برمی‌گردد.
        self.assertFalse(ok)
        self.assertEqual(SystemErrorLog.objects.count(), 0)
        self.assertTrue(any("fail-open" in line for line in logs.output))


# ==================================================================
# 2) middleware گزارش خطا (پوشش View / API / چرخهٔ سفارش)
# ==================================================================
class ErrorReportingMiddlewareTests(TestCase):

    def setUp(self):
        self.factory = RequestFactory()
        self.mw = SystemErrorReportingMiddleware(get_response=lambda r: None)

    def test_unhandled_exception_is_recorded(self):
        request = self.factory.get("/api/orders/99/accept/")
        exc = RuntimeError("internal failure")

        result = self.mw.process_exception(request, exc)

        self.assertIsNone(result)  # پاسخ را تغییر نمی‌دهد
        row = SystemErrorLog.objects.get(code="unhandled_exception")
        self.assertEqual(row.source, "order")
        self.assertEqual(row.path, "/api/orders/99/accept/")
        self.assertIn("RuntimeError", row.message)
        self.assertIn("internal failure", row.message)
        self.assertTrue(getattr(request, "_system_error_recorded", False))

    def test_benign_exceptions_are_not_recorded(self):
        request = self.factory.get("/missing-page/")
        self.mw.process_exception(request, Http404("not found"))
        self.assertEqual(SystemErrorLog.objects.count(), 0)

    def test_5xx_response_is_recorded_once_per_request(self):
        request = self.factory.get("/api/create_order/")
        response = JsonResponse({"success": False}, status=500)

        self.mw.process_response(request, response)
        row = SystemErrorLog.objects.get(code="http_500_response")
        self.assertEqual(row.source, "order")   # مسیر /api/… ⇒ لایهٔ سفارش
        self.assertEqual(row.level, "CRITICAL")
        self.assertEqual(row.path, "/api/create_order/")

        # همان درخواست دوباره (flag) ⇒ بدون ردیف تکراری
        self.mw.process_response(
            request, JsonResponse({"success": False}, status=500)
        )
        self.assertEqual(SystemErrorLog.objects.count(), 1)

        # درخواست جدید با همان code+source+identifier ⇒ deduplicate می‌شود
        # (به‌جای ردیف جدید، شمارش تکرار بالا می‌رود تا لاگ انبوه نشود)
        self.mw.process_response(
            self.factory.get("/api/create_order/"),
            JsonResponse({"success": False}, status=500),
        )
        self.assertEqual(SystemErrorLog.objects.count(), 1)
        self.assertEqual(SystemErrorLog.objects.get().occurrences, 2)

        # مسیر متفاوت ⇒ identifier متفاوت ⇒ ردیف جدید
        self.mw.process_response(
            self.factory.get("/api/orders/3/send/"),
            JsonResponse({"success": False}, status=500),
        )
        self.assertEqual(SystemErrorLog.objects.count(), 2)

    def test_successful_response_is_not_recorded(self):
        request = self.factory.get("/dashboard/")
        self.mw.process_response(request, JsonResponse({}, status=200))
        self.assertEqual(SystemErrorLog.objects.count(), 0)

    def test_source_mapping_by_path(self):
        self.assertEqual(source_for_path("/api/missions/dispatch/"), "mission")
        self.assertEqual(source_for_path("/api/create_order/"), "order")
        self.assertEqual(source_for_path("/api/orders/3/ready/"), "order")
        self.assertEqual(source_for_path("/api/foods/"), "api")
        self.assertEqual(source_for_path("/dashboard/"), "view")

    @override_settings(ROOT_URLCONF="restaurant.tests_error_logs")
    def test_end_to_end_unhandled_view_exception(self):
        client = Client()
        client.raise_request_exception = False

        response = client.get("/_boom/")

        self.assertEqual(response.status_code, 500)
        row = SystemErrorLog.objects.get(code="unhandled_exception")
        self.assertEqual(row.path, "/_boom/")
        self.assertIn("RuntimeError", row.message)


# ==================================================================
# 3) پوشش لایهٔ مأموریت (شکست transport هنگام dispatch)
# ==================================================================
class MissionLayerRecordingTests(TestCase):

    class _TimeoutTransport:
        def outcome_for(self, mission_uid):
            return None

        def send_move_command(self, command, mission_uid=None, robot_id=None):
            raise RobotTimeoutError("robot did not answer in time")

    def _mission(self):
        order = Order.objects.create(table="5", total=100, status="ready")
        return Mission.objects.create(
            order=order,
            destination_table="5",
            robot_id=ROBOT_ID,
            status=STATUS_PENDING,
        )

    def test_dispatch_timeout_is_recorded_in_system_error_log(self):
        mission = self._mission()

        with self.assertRaises(DispatchError):
            dispatch_mission(
                mission_uid=mission.mission_uid,
                transport=self._TimeoutTransport(),
            )

        row = SystemErrorLog.objects.get(code="robot_timeout")
        self.assertEqual(row.source, "mission")
        self.assertEqual(row.mission_uid, str(mission.mission_uid))
        self.assertEqual(row.robot_id, ROBOT_ID)
        self.assertEqual(row.path, "restaurant.mission_dispatch")


# ==================================================================
# 4) پوشش لایهٔ WebSocket (consumer ربات)
# ==================================================================
class WebsocketLayerRecordingTests(TestCase):

    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_robot_consumer_records_missing_mission_id(self):
        async def run():
            communicator = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token={TOKEN}",
            )
            connected, _subprotocol = await communicator.connect(timeout=2)
            self.assertTrue(connected)
            # حذف قاب «connected» قبل از ارسال گزارش
            await communicator.receive_json_from(timeout=2)
            await communicator.send_json_to(
                {"event": "ack", "command_id": "cmd-1"}
            )
            response = await communicator.receive_json_from(timeout=2)
            await communicator.disconnect()
            return response

        response = async_to_sync(run)()

        self.assertEqual(
            response, {"event": "error", "code": "missing_mission_id"}
        )
        row = SystemErrorLog.objects.get(code="missing_mission_id")
        self.assertEqual(row.source, "websocket")
        self.assertEqual(row.robot_id, ROBOT_ID)
        self.assertEqual(row.path, f"ws/robots/{ROBOT_ID}")


# ==================================================================
# 5) داشبورد مدیریتی (دسترسی + فیلترها + عدم نشت PII)
# ==================================================================
class SystemErrorsDashboardTests(TestCase):

    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="chef-errors", password="chef-pass-123", is_staff=True
        )
        self.customer = User.objects.create_user(
            username="guest-errors", password="guest-pass-123"
        )
        self.staff_client = Client(enforce_csrf_checks=False)
        self.staff_client.force_login(self.staff)
        self.customer_client = Client(enforce_csrf_checks=False)
        self.customer_client.force_login(self.customer)

        self.mission_uid = str(uuid.uuid4())
        record_system_error(
            level="ERROR", source="mission", code="robot_timeout",
            message="timeout", mission_uid=self.mission_uid,
        )
        record_system_error(
            level="WARNING", source="websocket", code="missing_mission_id",
            message="no mission id", robot_id="RBOT-01",
        )
        record_system_error(
            level="CRITICAL", source="order", code="http_500_response",
            message="HTTP 500", order_uid="5", path="/api/create_order/",
        )

    def test_anonymous_and_non_staff_are_redirected_to_login(self):
        anonymous = Client().get("/dashboard/errors/")
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/admin/login/", anonymous.url)

        customer = self.customer_client.get("/dashboard/errors/")
        self.assertEqual(customer.status_code, 302)
        self.assertIn("/admin/login/", customer.url)

    def test_staff_sees_table_and_filters_work(self):
        response = self.staff_client.get("/dashboard/errors/")
        self.assertEqual(response.status_code, 200)
        page = response.content.decode()
        self.assertIn("گزارش خطاهای سیستم", page)
        self.assertIn("robot_timeout", page)
        self.assertIn("missing_mission_id", page)

        # فیلتر سطح
        page = self.staff_client.get(
            "/dashboard/errors/", {"level": "CRITICAL"}
        ).content.decode()
        self.assertIn("http_500_response", page)
        self.assertNotIn("robot_timeout", page)

        # فیلتر منبع
        page = self.staff_client.get(
            "/dashboard/errors/", {"source": "websocket"}
        ).content.decode()
        self.assertIn("missing_mission_id", page)
        self.assertNotIn("robot_timeout", page)

        # فیلتر mission_uid
        page = self.staff_client.get(
            "/dashboard/errors/", {"mission_uid": self.mission_uid}
        ).content.decode()
        self.assertIn("robot_timeout", page)
        self.assertNotIn("http_500_response", page)

        # فیلتر robot_id
        page = self.staff_client.get(
            "/dashboard/errors/", {"robot_id": "RBOT-01"}
        ).content.decode()
        self.assertIn("missing_mission_id", page)
        self.assertNotIn("robot_timeout", page)

    def test_dashboard_never_leaks_sensitive_data(self):
        phone = "09121112233"
        record_system_error(
            level="ERROR", source="api", code="pii_attempt",
            message=f"شکست برای {phone} با token=abcdef123456",
        )

        page = self.staff_client.get(
            "/dashboard/errors/"
        ).content.decode()

        self.assertNotIn(phone, page)
        self.assertNotIn("abcdef123456", page)
        self.assertIn("[PHONE]", page)
        self.assertIn("[REDACTED]", page)


# ==================================================================
# 6) اتصال Django logging به SystemErrorLog (LOGGING در settings)
# ==================================================================
class LoggingHandlerTests(TestCase):

    def test_restaurant_warning_logger_is_recorded(self):
        logging.getLogger("restaurant.demo_module").warning(
            "warn from app logger"
        )

        row = SystemErrorLog.objects.get(code="log_warning")
        self.assertEqual(row.source, "other")
        self.assertEqual(row.level, "WARNING")
        self.assertIn("warn from app logger", row.message)
