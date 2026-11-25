"""
Reference WebSocket client for an EnjiNex waiter robot (section 14, item 3/5).

This is a *reference implementation* the Embedded team can port to ESP32/STM32
firmware. It is NOT required for the Django server to work; it exists so the
robot side of the contract is concrete and an End-to-End smoke test can be run
against a real server.

Protocol (see restaurant/robot_consumer.py and MISSION_API_DOCS.md §10):
  Connect : ws://<host>/ws/robots/<robot_id>/?token=<ROBOT_API_TOKEN>
  Server -> robot : {"event":"connected"}
                    {"event":"command","command":{...Movement Command...}}
                    {"event":"ack_received",...}
                    {"event":"error","code":...}
                    {"event":"pong"}
  robot -> server : {"event":"ack","command_id":...,"mission_id":...,"status":"accepted"}
                    {"event":"status","command_id":...,"mission_id":...,"status":"in_progress"}
                    {"event":"completed","command_id":...,"mission_id":...,"status":"completed"}
                    {"event":"reject","command_id":...,"mission_id":...,"status":"rejected",
                                     "error_code":...,"error_message":...}
                    {"event":"ping"}

Required robot fields in every report: command_id + mission_id (both come from
the received command). A robot may only report on its own mission.

Usage:
  pip install websockets
  python scripts/robot_client_reference.py \
      --host 127.0.0.1:8000 --robot-id RBOT-01 --token "$ROBOT_API_TOKEN"

  # against a TLS server:
  python scripts/robot_client_reference.py --host example.com --tls ...

The --auto flag simulates a full delivery cycle (accepted -> in_progress ->
completed) so you can smoke-test the server without real hardware.
"""

import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:  # pragma: no cover - reference helper
    sys.stderr.write(
        "The 'websockets' package is required: pip install websockets\n"
    )
    raise


async def _report(ws, event, command, status, **extra):
    """Send one status/ack report for a command."""
    payload = {
        "event": event,
        "command_id": command.get("command_id"),
        "mission_id": command.get("mission_id"),
        "status": status,
    }
    payload.update(extra)
    await ws.send(json.dumps(payload))
    print(f"  -> {event}: {status}")


async def _handle_command(ws, command, auto):
    print(f"  <- command {command.get('command_id')} "
          f"{command.get('command_type')} -> {command.get('destination_id')}")
    if not auto:
        # Real firmware: enqueue motion, then report progress as it happens.
        print("     (manual mode: not auto-reporting; pass --auto to simulate)")
        return
    await _report(ws, "ack", command, "accepted")
    await asyncio.sleep(0.2)
    await _report(ws, "status", command, "in_progress")
    await asyncio.sleep(0.5)
    await _report(ws, "completed", command, "completed")


async def run(host, robot_id, token, tls, auto):
    scheme = "wss" if tls else "ws"
    url = f"{scheme}://{host}/ws/robots/{robot_id}/?token={token}"
    print(f"Connecting to {scheme}://<host>/ws/robots/{robot_id}/ (token hidden)")
    async with websockets.connect(url) as ws:
        async def reader():
            async for raw in ws:
                msg = json.loads(raw)
                event = msg.get("event")
                if event == "connected":
                    print(f"  <- connected as {msg.get('robot_id')}")
                elif event == "command":
                    await _handle_command(ws, msg.get("command") or {}, auto)
                elif event == "ack_received":
                    print(f"  <- server ack_received: {msg.get('status')}")
                elif event == "pong":
                    print("  <- pong")
                elif event == "error":
                    print(f"  <- server error: {msg.get('code')}")
                else:
                    print(f"  <- {msg}")

        async def keepalive():
            while True:
                await asyncio.sleep(25)
                await ws.send(json.dumps({"event": "ping"}))

        await asyncio.gather(reader(), keepalive())


def main():
    p = argparse.ArgumentParser(description="EnjiNex reference robot WS client")
    p.add_argument("--host", required=True, help="host:port, e.g. 127.0.0.1:8000")
    p.add_argument("--robot-id", required=True)
    p.add_argument("--token", required=True, help="ROBOT_API_TOKEN")
    p.add_argument("--tls", action="store_true", help="use wss://")
    p.add_argument("--auto", action="store_true",
                   help="simulate accepted->in_progress->completed")
    args = p.parse_args()
    try:
        asyncio.run(run(args.host, args.robot_id, args.token, args.tls, args.auto))
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()