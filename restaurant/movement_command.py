"""
قرارداد استاندارد Movement Command (تأییدشده توسط مدیر عامل، نسخه ۱.۰).

این ماژول تنها منبع ساخت و اعتبارسنجی فرمان حرکت ربات است.
هیچ کد دیگری نباید ساختار JSON جداگانهای برای فرمان حرکت بسازد.

ساختار تأییدشده:

    {
        "protocol_version": "1.0",
        "command_id": "<uuid>",
        "robot_id": "<robot id>",
        "command_type": "MOVE_TO_TABLE",
        "mission_id": "<mission_uid>",
        "destination_id": "TABLE_04",
        "created_at": "<ISO-8601>",
        "parameters": { ... }
    }

نگاشت مقصد: destination_table (عدد ۱ .. FLOOR_TOTAL_TABLES) → TABLE_01 .. TABLE_<N>.
"""

from datetime import datetime, timezone as dt_timezone
import uuid

from .table_config import (
    FIRST_TABLE_NUMBER as MIN_TABLE_NUMBER,
    get_max_table_number,
)

PROTOCOL_VERSION = "1.0"
COMMAND_TYPE_MOVE_TO_TABLE = "MOVE_TO_TABLE"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({PROTOCOL_VERSION})

TABLE_PREFIX = "TABLE_"
# نام قدیمی برای سازگاری؛ مقدار آن از FLOOR_TOTAL_TABLES خوانده میشود.
MAX_TABLE_NUMBER = get_max_table_number()


class InvalidDestinationError(ValueError):
    """مقصد مأموریت قابل نگاشت به destination_id استاندارد نیست."""


class UnsupportedProtocolVersionError(ValueError):
    """نسخهٔ قرارداد ارتباطی پشتیبانی نمیشود."""


class CommandValidationError(ValueError):
    """ساختار فرمان نامعتبر است."""


def normalize_table_number(value):
    """تبدیل شمارهٔ میز به عدد صحیح معتبر (۱ .. FLOOR_TOTAL_TABLES) یا خطا."""
    if value is None:
        raise InvalidDestinationError("شمارهٔ میز مقصد وجود ندارد.")
    try:
        table_num = int(str(value).strip())
    except (TypeError, ValueError):
        raise InvalidDestinationError(
            f"شمارهٔ میز مقصد باید عدد صحیح باشد، نه {value!r}."
        )
    max_table = get_max_table_number()
    if not (MIN_TABLE_NUMBER <= table_num <= max_table):
        raise InvalidDestinationError(
            f"شمارهٔ میز مقصد باید بین {MIN_TABLE_NUMBER} و {max_table} "
            f"باشد، نه {value!r}."
        )
    return table_num


def to_destination_id(value):
    """نگاشت واحد و قابلمدیریت: ۴ → TABLE_04."""
    table_num = normalize_table_number(value)
    return f"{TABLE_PREFIX}{table_num:02d}"


def from_destination_id(destination_id):
    """تجزیهٔ destination_id استاندارد به شمارهٔ میز (برای پاسخ ربات)."""
    if not isinstance(destination_id, str):
        raise InvalidDestinationError("destination_id باید رشته باشد.")
    if not destination_id.startswith(TABLE_PREFIX):
        raise InvalidDestinationError(
            f"destination_id باید با {TABLE_PREFIX!r} شروع شود: {destination_id!r}"
        )
    number_part = destination_id[len(TABLE_PREFIX):]
    if not number_part.isdigit() or len(number_part) != 2:
        raise InvalidDestinationError(
            f"destination_id باید TABLE_<NN> باشد: {destination_id!r}"
        )
    return normalize_table_number(int(number_part))


def build_move_command(*, mission_uid, robot_id, destination_table, parameters=None,
                       command_id=None, created_at=None, protocol_version=PROTOCOL_VERSION):
    """
    ساخت فرمان استاندارد Move-To-Table از اطلاعات معتبر مأموریت.

    mission_uid   : UUID مأموریت (mission.mission_uid)
    robot_id      : شناسهٔ ربات مقصد (غیرخالی)
    destination_table : شمارهٔ میز (اعتبارسنجی و نگاشت میشود)
    parameters    : دیکشنری پارامترهای حرکتی (اختیاری)
    """
    if protocol_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise UnsupportedProtocolVersionError(
            f"نسخهٔ قرارداد پشتیبانی نشد: {protocol_version!r}"
        )

    mission_uid_str = str(mission_uid or "").strip()
    if not mission_uid_str:
        raise CommandValidationError("mission_id (mission_uid) نمیتواند خالی باشد.")
    try:
        uuid.UUID(mission_uid_str)
    except (ValueError, AttributeError, TypeError):
        raise CommandValidationError(f"mission_id یک UUID معتبر نیست: {mission_uid_str!r}")

    robot_id_str = str(robot_id or "").strip()
    if not robot_id_str:
        raise CommandValidationError("robot_id نمیتواند خالی باشد.")

    destination_id = to_destination_id(destination_table)

    if parameters is None:
        parameters = {}
    if not isinstance(parameters, dict):
        raise CommandValidationError("parameters باید یک شیء (dict) باشد.")

    if created_at is None:
        created_at = datetime.now(dt_timezone.utc)
    if isinstance(created_at, datetime):
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=dt_timezone.utc)
        created_at_str = created_at.isoformat()
    else:
        created_at_str = str(created_at)

    return {
        "protocol_version": protocol_version,
        "command_id": str(command_id or uuid.uuid4()),
        "robot_id": robot_id_str,
        "command_type": COMMAND_TYPE_MOVE_TO_TABLE,
        "mission_id": mission_uid_str,
        "destination_id": destination_id,
        "created_at": created_at_str,
        "parameters": parameters,
    }


def validate_command(command):
    """اعتبارسنجی ساختار یک فرمان دریافتی/ارسالشده. در صورت خطا raise میکند."""
    if not isinstance(command, dict):
        raise CommandValidationError("فرمان باید یک شیء JSON باشد.")

    required = {
        "protocol_version", "command_id", "robot_id", "command_type",
        "mission_id", "destination_id", "created_at", "parameters",
    }
    missing = required - set(command.keys())
    if missing:
        raise CommandValidationError(f"فیلدهای الزامی فرمان وجود ندارند: {sorted(missing)}")

    if command["protocol_version"] not in SUPPORTED_PROTOCOL_VERSIONS:
        raise UnsupportedProtocolVersionError(
            f"نسخهٔ قرارداد پشتیبانی نشد: {command['protocol_version']!r}"
        )

    if command["command_type"] != COMMAND_TYPE_MOVE_TO_TABLE:
        raise CommandValidationError(
            f"command_type نامعتبر: {command['command_type']!r}"
        )

    if not str(command["robot_id"] or "").strip():
        raise CommandValidationError("robot_id نمیتواند خالی باشد.")

    try:
        uuid.UUID(str(command["command_id"]))
    except (ValueError, AttributeError, TypeError):
        raise CommandValidationError("command_id یک UUID معتبر نیست.")

    try:
        uuid.UUID(str(command["mission_id"]))
    except (ValueError, AttributeError, TypeError):
        raise CommandValidationError("mission_id یک UUID معتبر نیست.")

    from_destination_id(command["destination_id"])

    if not isinstance(command["parameters"], dict):
        raise CommandValidationError("parameters باید یک شیء (dict) باشد.")

    if not str(command["created_at"] or "").strip():
        raise CommandValidationError("created_at نمیتواند خالی باشد.")

    return True
