"""
Local End-to-End smoke test (section 14, item 5) with the reference client's
--auto behaviour, run fully in-process against the REAL ASGI app + channel
layer (no Redis, no hardware needed).

It exercises the complete server-side loop:
  kitchen dispatch_mission()  ->  ChannelRobotTransport  ->  channel layer
  ->  RobotConsumer receives the Movement Command
  ->  robot auto-reports accepted -> in_progress -> completed
  ->  Mission status ends COMPLETED.

Run:
  python manage.py test restaurant.tests_mission_e2e_smoke -v 2
"""

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.test import TestCase, override_settings

from restaurant import mission_statuses as ms
from restaurant.mission_dispatch import dispatch_mission
from restaurant.models import Mission, Order
from restaurant.robot_transport import ChannelRobotTransport, set_robot_transport
from RestaurantRobot.asgi import application

ROBOT_ID = "RBOT-01"
TOKEN = "e2e-smoke-token-2026"


class E2ESmokeAutoCycle(TestCase):
    @override_settings(ROBOT_API_TOKEN=TOKEN)
    def test_auto_cycle_reaches_completed(self):
        # The real --auto cycle needs the channels transport so dispatch reaches
        # the WebSocket group; the default in-memory transport never broadcasts.
        set_robot_transport(ChannelRobotTransport())
        self.addCleanup(set_robot_transport, None)
        Order.objects.all().delete()
        Mission.objects.all().delete()
        order = Order.objects.create(table="4", total=100, status="ready")
        mission = Mission.objects.create(
            order=order, destination_table="4",
            robot_id=ROBOT_ID, status=ms.STATUS_PENDING,
        )
        mission_uid = str(mission.mission_uid)
        seen = []

        async def run():
            comm = WebsocketCommunicator(
                application, f"/ws/robots/{ROBOT_ID}/?token={TOKEN}",
            )
            connected, _ = await comm.connect(timeout=2)
            assert connected, "robot WS not connected"
            seen.append(("connected", (await comm.receive_json_from(timeout=2)).get("event")))

            # Kitchen dispatches (sync ORM -> thread), fans out over the channel layer.
            result = await __import__("asgiref.sync", fromlist=["sync_to_async"]).sync_to_async(
                dispatch_mission)(mission_uid=mission_uid)
            seen.append(("dispatch", result.get("command_status")))

            cmd_frame = await comm.receive_json_from(timeout=2)
            command = cmd_frame.get("command") or {}
            seen.append(("command", command.get("destination_id")))

            # Reference client --auto sequence.
            for ev, st in (("ack", "accepted"),
                           ("status", "in_progress"),
                           ("completed", "completed")):
                await comm.send_json_to({
                    "event": ev, "command_id": command.get("command_id"),
                    "mission_id": command.get("mission_id"), "status": st,
                })
                ack = await comm.receive_json_from(timeout=2)
                seen.append((ev, ack.get("mission_status")))

            await comm.disconnect()

        async_to_sync(run)()
        final = Mission.objects.get(mission_uid=mission_uid).status
        print("\n[E2E --auto] steps:", seen)
        print("[E2E --auto] final mission status:", final)
        self.assertEqual(final, ms.STATUS_COMPLETED)