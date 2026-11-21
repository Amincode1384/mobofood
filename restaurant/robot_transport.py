"""
Robot communication layer. Separates "building a command" from "sending it".

Implementations:
  - InMemoryRobotTransport: simulator / default for development and tests.
  - ChannelRobotTransport: broadcasts commands to the robot's WebSocket group.

Select the backend via settings.ROBOT_TRANSPORT_BACKEND:
    "memory"   -> InMemoryRobotTransport (default)
    "channels" -> ChannelRobotTransport

Never assume a command reached the robot just because a DB write succeeded.
The transport returns the real transport result (accepted / rejected / timeout).
"""

import uuid
from datetime import datetime, timezone as dt_timezone

from django.conf import settings


class RobotTransportError(Exception):
    """Base class for transport errors."""


class RobotDisconnectedError(RobotTransportError):
    """Robot link is down."""


class RobotTimeoutError(RobotTransportError):
    """No response within the timeout window."""


class InvalidRobotResponseError(RobotTransportError):
    """Robot response is invalid."""


class WrongCommandIdError(RobotTransportError):
    """Response belongs to a different command."""


class DuplicateResponseError(RobotTransportError):
    """Duplicate response for the same command."""


class RobotRejectedCommandError(RobotTransportError):
    """Robot rejected the command."""


# ---------------------------------------------------------------------------
# Outcome store: idempotency and no double execution
# ---------------------------------------------------------------------------

class CommandOutcome:
    """Result of sending one command to one robot."""

    def __init__(self, command_id, status, received=False, accepted=False,
                 error_code=None, error_message=None, responded_at=None):
        self.command_id = str(command_id)
        self.status = status  # accepted | received | rejected | timeout | disconnected | sent
        self.received = bool(received)
        self.accepted = bool(accepted)
        self.error_code = error_code
        self.error_message = error_message
        self.responded_at = responded_at or datetime.now(dt_timezone.utc).isoformat()

    def as_dict(self):
        return {
            "command_id": self.command_id,
            "status": self.status,
            "received": self.received,
            "accepted": self.accepted,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "responded_at": self.responded_at,
        }


class InMemoryOutcomeStore:
    """In-memory outcome store (single-process / tests).

    Keyed by mission_uid so a mission is sent at most once.
    """

    def __init__(self):
        self._by_mission = {}

    def get(self, mission_uid):
        return self._by_mission.get(str(mission_uid))

    def put(self, mission_uid, outcome):
        self._by_mission[str(mission_uid)] = outcome

    def clear(self):
        self._by_mission.clear()


# ---------------------------------------------------------------------------
# Transport interface
# ---------------------------------------------------------------------------

class RobotTransport:
    """Common transport interface."""

    def send_move_command(self, command, *, mission_uid, robot_id):
        raise NotImplementedError

    def acknowledge(self, *, command_id, robot_id, status, error_code=None, error_message=None):
        raise NotImplementedError

    def outcome_for(self, mission_uid):
        raise NotImplementedError


class InMemoryRobotTransport(RobotTransport):
    """Simulated robot: records commands and returns a configurable outcome."""

    def __init__(self, outcome_store=None, default_status="accepted"):
        self.outcomes = outcome_store or InMemoryOutcomeStore()
        self.sent_commands = []
        self.default_status = default_status
        self._queued_responses = []

    def queue_response(self, command_id, *, status="accepted", received=True,
                       accepted=None, error_code=None, error_message=None,
                       robot_id=None):
        if accepted is None:
            accepted = status in ("accepted", "received")
        self._queued_responses.append({
            "command_id": str(command_id),
            "status": status,
            "received": received,
            "accepted": accepted,
            "error_code": error_code,
            "error_message": error_message,
            "robot_id": robot_id,
        })

    def send_move_command(self, command, *, mission_uid, robot_id):
        from .movement_command import validate_command

        validate_command(command)
        if command["robot_id"] != str(robot_id):
            raise RobotTransportError("command robot_id does not match destination robot_id")

        previous = self.outcomes.get(mission_uid)
        if previous is not None:
            return previous  # Idempotent: no second send

        self.sent_commands.append(command)

        queued = None
        for i, candidate in enumerate(self._queued_responses):
            if candidate["command_id"] == command["command_id"]:
                queued = self._queued_responses.pop(i)
                break

        if queued is not None:
            outcome = CommandOutcome(
                command["command_id"], queued["status"],
                received=queued["received"], accepted=queued["accepted"],
                error_code=queued["error_code"], error_message=queued["error_message"],
            )
        elif self.default_status == "disconnected":
            raise RobotDisconnectedError("robot link is down")
        elif self.default_status == "timeout":
            raise RobotTimeoutError("robot did not respond within the timeout window")
        else:
            accepted = self.default_status == "accepted"
            outcome = CommandOutcome(
                command["command_id"],
                "accepted" if accepted else "rejected",
                received=True, accepted=accepted,
                error_code=None if accepted else "robot_rejected",
                error_message=None if accepted else "robot rejected the command",
            )

        self.outcomes.put(mission_uid, outcome)
        return outcome

    def acknowledge(self, *, command_id, robot_id, status, error_code=None, error_message=None):
        accepted = status in ("accepted", "received", "completed")
        return CommandOutcome(
            command_id, status,
            received=status in ("accepted", "received", "completed"),
            accepted=accepted, error_code=error_code, error_message=error_message,
        )

    def outcome_for(self, mission_uid):
        return self.outcomes.get(mission_uid)


class ChannelRobotTransport(RobotTransport):
    """Sends commands through the Channel Layer to robot_<robot_id> group."""

    GROUP_PREFIX = "robot_"

    def __init__(self, outcome_store=None, timeout_seconds=None):
        self.outcomes = outcome_store or InMemoryOutcomeStore()
        self.timeout_seconds = timeout_seconds or getattr(settings, "ROBOT_ACK_TIMEOUT_SECONDS", 5)

    def send_move_command(self, command, *, mission_uid, robot_id):
        from .movement_command import validate_command
        from .events import broadcast_robot_command

        validate_command(command)
        previous = self.outcomes.get(mission_uid)
        if previous is not None:
            return previous

        try:
            broadcast_robot_command(robot_id, command)
        except Exception as exc:
            raise RobotDisconnectedError(f"failed to deliver command: {exc}") from exc

        outcome = CommandOutcome(
            command["command_id"], "sent", received=False, accepted=False,
        )
        self.outcomes.put(mission_uid, outcome)
        return outcome

    def acknowledge(self, *, command_id, robot_id, status, error_code=None, error_message=None):
        accepted = status in ("accepted", "received", "completed")
        return CommandOutcome(
            command_id, status,
            received=status in ("accepted", "received", "completed"),
            accepted=accepted, error_code=error_code, error_message=error_message,
        )

    def outcome_for(self, mission_uid):
        return self.outcomes.get(mission_uid)

    def record_for_mission(self, mission_uid, outcome):
        self.outcomes.put(mission_uid, outcome)


_SHARED_TRANSPORT = None


def get_robot_transport():
    global _SHARED_TRANSPORT
    if _SHARED_TRANSPORT is not None:
        return _SHARED_TRANSPORT
    backend = getattr(settings, "ROBOT_TRANSPORT_BACKEND", "memory").lower()
    if backend == "memory":
        _SHARED_TRANSPORT = InMemoryRobotTransport()
    elif backend == "channels":
        _SHARED_TRANSPORT = ChannelRobotTransport()
    else:
        raise RobotTransportError(f"unknown ROBOT_TRANSPORT_BACKEND: {backend!r}")
    return _SHARED_TRANSPORT


def set_robot_transport(transport):
    """Inject a custom transport (used by tests)."""
    global _SHARED_TRANSPORT
    _SHARED_TRANSPORT = transport
