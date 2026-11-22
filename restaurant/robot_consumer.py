"""
WebSocket consumer for waiter robots.

Connection:  ws/robots/<robot_id>/?token=<ROBOT_API_TOKEN>
Each robot is placed in its own group (robot_<robot_id>) and can only ever
receive commands addressed to that robot_id. Inbound messages are status
reports / acknowledgements for commands previously sent to that robot.

Auth: a shared secret ROBOT_API_TOKEN from settings (env in production), plus
the URL robot_id must match the token's robot scope. If no token is configured,
connections are rejected (fail closed).
"""

import hmac
from datetime import datetime, timezone as dt_timezone

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.db import database_sync_to_async

from django.conf import settings
from django.core.exceptions import ValidationError

from .events import robot_group_name
from . import mission_statuses as mission_status
from .error_logs import record_system_error
from .models import Mission


class RobotConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.robot_id = self.scope["url_route"]["kwargs"]["robot_id"]
        if not self._authenticate():
            await self.close(code=4401)
            return
        self.group_name = robot_group_name(self.robot_id)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()
        await self.send_json({"event": "connected", "robot_id": self.robot_id})

    def _authenticate(self):
        expected = getattr(settings, "ROBOT_API_TOKEN", "")
        if not expected:
            return False
        token = self.scope.get("query_string", b"").decode().strip()
        # Accept ?token=... or a bare token in the query string.
        if token.startswith("token="):
            token = token.split("=", 1)[1]
        return hmac.compare_digest(token, str(expected))

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def robot_command(self, event):
        """Forward a movement command to this robot only."""
        await self.send_json({"event": "command", "command": event["command"]})

    async def receive_json(self, content, **kwargs):
        """Handle robot status/ack reports."""
        try:
            await self._handle_inbound(content, **kwargs)
        except Exception as exc:
            # ثبت خطا در SystemErrorLog؛ سپس همان رفتار قبلی (بالا رفتن
            # استثنا و بسته‌شدن اتصال) بدون تغییر حفظ می‌شود.
            await self._record_error(
                level="ERROR",
                code="websocket_receive_failed",
                message=f"{type(exc).__name__}: {exc}",
            )
            raise

    async def _handle_inbound(self, content, **kwargs):
        event = content.get("event") or content.get("type")
        if event in ("ack", "status", "reject", "completed"):
            command_id = content.get("command_id")
            status = (content.get("status") or event).lower()
            await self._handle_ack(command_id, status, content)
        elif event == "ping":
            await self.send_json({"event": "pong"})

    async def _reject(self, code, message, mission_uid=""):
        """
        پاسخ خطا به ربات (همان JSON قبلی) + ثبت WARNING در SystemErrorLog.
        """
        await self._record_error(
            level="WARNING",
            code=code,
            message=message or code,
            mission_uid=mission_uid or "",
        )
        await self.send_json({"event": "error", "code": code})

    @database_sync_to_async
    def _record_error(self, *, level, code, message, mission_uid=""):
        """ثبت خطای لایهٔ WebSocket در SystemErrorLog (fail-open)."""
        robot_id = getattr(self, "robot_id", "") or ""
        record_system_error(
            level=level,
            source="websocket",
            code=code,
            message=message,
            mission_uid=mission_uid or "",
            robot_id=robot_id,
            path=f"ws/robots/{robot_id}",
        )

    async def _handle_ack(self, command_id, status, content):
        from .robot_transport import (
            WrongCommandIdError, DuplicateResponseError, get_robot_transport,
        )

        mission_uid = content.get("mission_id") or content.get("mission_uid")
        if not mission_uid:
            await self._reject(
                "missing_mission_id", "گزارش ربات شناسهٔ مأموریت ندارد."
            )
            return

        mission = await self._get_mission(mission_uid)
        if mission is None:
            await self._reject(
                "mission_not_found",
                "مأموریت موردنظر پیدا نشد.",
                mission_uid=mission_uid,
            )
            return

        # Robot may only report on its own mission.
        if str(mission.robot_id or "") != self.robot_id:
            await self._reject(
                "robot_scope_violation",
                "ربات فقط می‌تواند دربارهٔ مأموریت خودش گزارش دهد.",
                mission_uid=str(mission.mission_uid),
            )
            return

        transport = get_robot_transport()
        previous = transport.outcome_for(mission_uid)
        if previous is not None:
            if str(previous.command_id) != str(command_id):
                await self._reject(
                    "wrong_command_id",
                    "command_id با فرمان قبلی همخوانی ندارد.",
                    mission_uid=mission_uid,
                )
                return
            # «sent» = فرمان فرستاده شده اما هنوز تأییدنشده ⇒ تأییدِ اولیهٔ ربات برای همین فرمان است؛ ادامه بده.
            # هر وضعیت نهایی (accepted/rejected/completed/failed/timeout/disconnected) ⇒ پاسخ تکراری است.
            if previous.status != "sent":
                await self._reject(
                    "duplicate_response",
                    "پاسخ تکراری برای همین مأموریت.",
                    mission_uid=mission_uid,
                )
                return

        outcome = transport.acknowledge(
            command_id=command_id, robot_id=self.robot_id, status=status,
            error_code=content.get("error_code"),
            error_message=content.get("error_message"),
        )
        if hasattr(transport, "record_for_mission"):
            transport.record_for_mission(mission_uid, outcome)

        new_status = self._map_status(status, outcome)
        if new_status and mission_status.can_transition(mission.status, new_status):
            await self._update_mission(mission_uid, new_status, self.robot_id)

        await self.send_json({
            "event": "ack_received", "command_id": str(command_id),
            "status": status, "mission_status": new_status,
        })

    @staticmethod
    def _map_status(status, outcome):
        s = str(status).lower()
        if s in ("accepted", "received"):
            return mission_status.STATUS_ASSIGNED
        if s in ("in_progress", "moving", "started"):
            return mission_status.STATUS_IN_PROGRESS
        if s in ("completed", "delivered", "done"):
            return mission_status.STATUS_COMPLETED
        if s in ("rejected",):
            return mission_status.STATUS_FAILED
        if s in ("failed", "error"):
            return mission_status.STATUS_FAILED
        return None

    @database_sync_to_async
    def _get_mission(self, mission_uid):
        try:
            return Mission.objects.get(mission_uid=mission_uid)
        except (Mission.DoesNotExist, ValueError, ValidationError):
            return None

    @database_sync_to_async
    def _update_mission(self, mission_uid, new_status, robot_id):
        try:
            m = Mission.objects.get(mission_uid=mission_uid)
        except Mission.DoesNotExist:
            return
        m.status = new_status
        if new_status == mission_status.STATUS_ASSIGNED and not m.robot_id:
            m.robot_id = robot_id
        m.save(update_fields=["status", "robot_id"])
