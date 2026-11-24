"""
Mission dispatch service - the core of the standard mission-sending API.

Responsibilities:
  1. Validate the mission (exists, active robot, valid destination).
  2. Map mission -> standard Movement Command (movement_command.py).
  3. Send through the configured transport layer (robot_transport.py).
  4. Record the result and advance mission status based on the REAL robot
     response (not merely on a successful DB transaction).

Key rules from the spec:
  - Creating a mission, sending a command, and physically completing the move
    are three different events. Do not conflate them.
  - Receipt (RECEIVED/ACCEPTED) is separate from execution (IN_PROGRESS/COMPLETED).
  - Idempotency: a mission is sent at most once; resending returns the prior result.
  - A timeout does NOT prove the robot did not receive the command: never
    silently resend. The stored outcome is checked first.
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import Mission
from . import mission_statuses as mission_status
from .error_logs import record_system_error
from .movement_command import (
    build_move_command, InvalidDestinationError, CommandValidationError,
)
from .robot_transport import (
    get_robot_transport, RobotTransportError, RobotDisconnectedError,
    RobotTimeoutError, RobotRejectedCommandError,
)


def _record_transport_failure(exc, code, mission):
    """
    ثبت شکست ارسال در SystemErrorLog (لایهٔ mission).

    کاملاً fail-open است: اگر ثبت ممکن نشد، استثنای اصلی DispatchError
    همچنان بالا می‌رود و رفتار view تغییری نمی‌کند.
    """
    record_system_error(
        level="ERROR",
        source="mission",
        code=code,
        message=str(exc),
        mission_uid=str(mission.mission_uid),
        robot_id=getattr(mission, "robot_id", "") or "",
        path="restaurant.mission_dispatch",
    )


class DispatchError(Exception):
    """Base class with a stable error code and message."""

    code = "dispatch_error"
    http_status = 400

    def __init__(self, message, code=None, http_status=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if http_status:
            self.http_status = http_status


class MissionNotFoundError(DispatchError):
    code = "mission_not_found"
    http_status = 404


class MissionNotDispatchableError(DispatchError):
    code = "mission_not_dispatchable"
    http_status = 409


class RobotUnavailableError(DispatchError):
    code = "robot_unavailable"
    http_status = 409


class InvalidDestinationDispatchError(DispatchError):
    code = "invalid_destination"
    http_status = 400


class UnsupportedVersionDispatchError(DispatchError):
    code = "unsupported_protocol_version"
    http_status = 400


DISPATCHABLE_STATUSES = {
    mission_status.STATUS_PENDING,
    mission_status.STATUS_QUEUED,
    mission_status.STATUS_ASSIGNED,
}

# پیام‌های عمومی خطاهای مأموریت: هرگز جزئیات استثنا یا مقادیر داخلی را در
# پاسخ API یا فیلد error_message دیتابیس بازنمی‌گردانیم (کد خطا در error_type).
_MISSION_FAILURE_MESSAGES = {
    "timeout": "زمان اجرای مأموریت از حد مجاز عبور کرد.",
    "robot_communication_failed": "ارتباط با ربات قطع یا ناموفق بود.",
    "robot_rejected": "ربات اجرای مأموریت را رد کرد.",
}


def resolve_robot_id(mission, requested_robot_id=None):
    """Choose the destination robot. Stored robot_id wins; input must match it."""
    stored = (mission.robot_id or "").strip()
    requested = (requested_robot_id or "").strip()
    if stored and requested and stored != requested:
        raise DispatchError(
            "robot_id ارسالشده با ربات تخصیصیافته به مأموریت همخوانی ندارد.",
            code="robot_id_mismatch", http_status=409,
        )
    robot_id = requested or stored
    if not robot_id:
        raise RobotUnavailableError("ربات مقصد برای این مأموریت تعیین نشده است.")
    return robot_id


def dispatch_mission(*, mission_uid=None, mission_code=None, robot_id=None,
                     parameters=None, transport=None):
    """
    ارسال یک مأموریت به ربات گارسون.

    Exactly one of mission_uid / mission_code must identify the mission.
    All mission data (destination, order) is read from the DB, never trusted
    from the request.

    Returns a dict describing the result.
    """
    if not mission_uid and not mission_code:
        raise DispatchError("شناسهٔ مأموریت لازم است.", code="missing_mission_id")

    query = Mission.objects.select_related("order")
    try:
        if mission_uid:
            mission = query.get(mission_uid=mission_uid)
        else:
            mission = query.get(mission_code=mission_code)
    except (Mission.DoesNotExist, ValueError, ValidationError):
        raise MissionNotFoundError("مأموریت پیدا نشد.")

    transport = transport or get_robot_transport()

    # Idempotency: never resend a mission that already has an outcome.
    prior = transport.outcome_for(str(mission.mission_uid))
    if prior is not None:
        return {
            "success": True,
            "idempotent_replay": True,
            "mission_uid": str(mission.mission_uid),
            "mission_code": mission.mission_code,
            "command_id": prior.command_id,
            "robot_id": mission.robot_id,
            "command_status": prior.status,
            "mission_status": mission.status,
            "message": "این مأموریت پیشتر ارسال شده است؛ فرمان دوباره ارسال نشد.",
        }

    if mission.status not in DISPATCHABLE_STATUSES:
        raise MissionNotDispatchableError(
            f"مأموریت در وضعیت {mission.status!r} قابل ارسال نیست."
        )

    try:
        destination_robot = resolve_robot_id(mission, robot_id)
    except DispatchError:
        raise

    # Validate destination from DB (server-authoritative).
    try:
        command = build_move_command(
            mission_uid=mission.mission_uid,
            robot_id=destination_robot,
            destination_table=mission.destination_table,
            parameters=parameters or {},
        )
    except InvalidDestinationError as exc:
        raise InvalidDestinationDispatchError(str(exc))
    except CommandValidationError as exc:
        raise DispatchError(str(exc), code="invalid_command")
    except Exception as exc:
        # Unsupported protocol version etc.
        raise UnsupportedVersionDispatchError(str(exc))

    command_id = command["command_id"]

    # Mark SENDING before touching the network, in its own transaction.
    with transaction.atomic():
        Mission.objects.select_for_update().filter(pk=mission.pk).update(
            status=mission_status.STATUS_QUEUED,
            robot_id=destination_robot,
        )
        mission.status = mission_status.STATUS_QUEUED
        mission.robot_id = destination_robot

    # Network I/O happens OUTSIDE the DB transaction on purpose. A DB commit
    # is not proof the robot received anything.
    try:
        outcome = transport.send_move_command(
            command, mission_uid=str(mission.mission_uid), robot_id=destination_robot,
        )
    except RobotTimeoutError as exc:
        # Timeout is inconclusive: record FAILED with a timeout error but do
        # NOT auto-retry. The command may have been received.
        with transaction.atomic():
            Mission.objects.filter(pk=mission.pk).update(
                status=mission_status.STATUS_FAILED,
                error_type="timeout",
                error_message=_MISSION_FAILURE_MESSAGES.get(
                    "timeout",
                    "زمان اجرای مأموریت از حد مجاز عبور کرد.",
                ),
            )
        _record_transport_failure(exc, "robot_timeout", mission)
        raise DispatchError(
            "پاسخ ربات دریافت نشد (Timeout). وضعیت فرمان نامشخص است؛ ارسال مجدد خودکار انجام نشد.",
            code="robot_timeout", http_status=504,
        )
    except RobotDisconnectedError as exc:
        with transaction.atomic():
            Mission.objects.filter(pk=mission.pk).update(
                status=mission_status.STATUS_FAILED,
                error_type="robot_communication_failed",
                error_message=_MISSION_FAILURE_MESSAGES.get(
                    "robot_communication_failed",
                    "خطای ربات در انجام مأموریت رخ داد.",
                ),
            )
        _record_transport_failure(exc, "robot_disconnected", mission)
        raise DispatchError(
            "اتصال با ربات قطع بود.",
            code="robot_disconnected", http_status=503,
        )
    except RobotRejectedCommandError as exc:
        with transaction.atomic():
            Mission.objects.filter(pk=mission.pk).update(
                status=mission_status.STATUS_FAILED,
                error_type="robot_rejected",
                error_message=_MISSION_FAILURE_MESSAGES.get("robot_rejected", ""),
            )
        _record_transport_failure(exc, "robot_rejected", mission)
        raise DispatchError(
            "ربات فرمان را رد کرد.",
            code="robot_rejected", http_status=409,
        )
    except RobotTransportError as exc:
        with transaction.atomic():
            Mission.objects.filter(pk=mission.pk).update(
                status=mission_status.STATUS_FAILED,
                error_type="robot_communication_failed",
                error_message=_MISSION_FAILURE_MESSAGES.get(
                    "robot_communication_failed",
                    "خطای ربات در انجام مأموریت رخ داد.",
                ),
            )
        _record_transport_failure(exc, "transport_error", mission)
        raise DispatchError(
            "ارسال فرمان به ربات ناموفق بود.",
            code="transport_error", http_status=503,
        )

    received = bool(outcome.received)
    accepted = bool(outcome.accepted)
    mission_status_value = (
        mission_status.STATUS_ASSIGNED if received else mission_status.STATUS_QUEUED
    )

    # SENT: the transport layer accepted the command for delivery.
    # The persisted status must match the returned mission_status: ASSIGNED only
    # when the robot actually received the command; otherwise QUEUED.
    with transaction.atomic():
        Mission.objects.filter(pk=mission.pk).update(status=mission_status_value)
        mission.status = mission_status_value

    return {
        "success": True,
        "idempotent_replay": False,
        "valid": True,
        "mission_found": True,
        "robot_valid": True,
        "command_built": True,
        "command_sent": True,
        "command_id": command_id,
        "robot_id": destination_robot,
        "destination_id": command["destination_id"],
        "command": command,
        "received": received,
        "accepted": accepted,
        "command_status": outcome.status,
        "mission_status": mission_status_value,
        "message": "فرمان حرکت برای ربات ارسال شد.",
    }
