"""
Live End-to-End smoke test (section 14, item 5) for the robot API.

Runs a real Daphne server on localhost (single process, in-memory channel
layer), connects the real reference WebSocket client in --auto mode, dispatches
a mission over the HTTP API, and verifies the mission reaches COMPLETED.

No Redis or hardware required. Usage:  python scripts/e2e_live_smoke.py
"""

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"
TOKEN = "smoke-token"
ROBOT = "RBOT-01"
TABLE = "7"

# Must be set before Django settings are imported.
os.environ["ROBOT_TRANSPORT_BACKEND"] = "channels"
os.environ["ROBOT_API_TOKEN"] = TOKEN
os.environ["CHANNEL_LAYER_BACKEND"] = "memory"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "RestaurantRobot.settings")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import django  # noqa: E402
django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from restaurant.models import Mission, Order  # noqa: E402
from restaurant import mission_statuses as ms  # noqa: E402


def wait_port(port, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def csrf_from(jar):
    for cookie in jar:
        if cookie.name == "csrftoken":
            return cookie.value
    return None


def main():
    from daphne.server import Server
    from RestaurantRobot.asgi import application

    print("1) starting Daphne on 127.0.0.1:8765 (memory channel layer)")
    server = Server(
        application,
        endpoints=["tcp:port=8765:interface=127.0.0.1"],
        signal_handlers=False,
    )
    threading.Thread(target=server.run, daemon=True).start()
    if not wait_port(8765):
        raise SystemExit("Daphne did not start")

    User = get_user_model()
    user, _ = User.objects.get_or_create(
        username="e2e_staff",
        defaults={"is_staff": True, "is_superuser": True},
    )
    user.set_password("E2ePass123!")
    user.is_staff = True
    user.save()

    order = Order.objects.create(table=TABLE, total=100, status="ready")
    mission = Mission.objects.create(
        order=order, destination_table=TABLE, robot_id=ROBOT,
        status=ms.STATUS_PENDING,
    )
    print(f"2) created mission {mission.mission_code} (table {TABLE}, {ROBOT})")

    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    # Login over the network to obtain a staff session.
    opener.open(BASE + "/admin/login/", timeout=10).read()
    csrf = csrf_from(jar)
    login_data = urllib.parse.urlencode({
        "username": "e2e_staff",
        "password": "E2ePass123!",
        "csrfmiddlewaretoken": csrf,
        "next": "/",
    }).encode()
    opener.open(
        urllib.request.Request(
            BASE + "/admin/login/", data=login_data,
            headers={"Referer": BASE + "/admin/login/"},
        ),
        timeout=10,
    ).read()
    csrf = csrf_from(jar)  # rotated on login
    assert any(c.name == "sessionid" for c in jar), "login failed: no sessionid"
    print("3) logged in as staff over HTTP")

    print("4) starting reference robot client (--auto)")
    proc = subprocess.Popen(
        [sys.executable, "-u", "scripts/robot_client_reference.py",
         "--host", "127.0.0.1:8765", "--robot-id", ROBOT,
         "--token", TOKEN, "--auto"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    time.sleep(2)  # let the robot join its group

    body = json.dumps({"mission_code": mission.mission_code}).encode()
    req = urllib.request.Request(
        BASE + "/api/missions/dispatch/", data=body,
        headers={
            "Content-Type": "application/json",
            "X-CSRFToken": csrf,
            "Referer": BASE,
        },
    )
    resp = opener.open(req, timeout=10)
    dispatch_result = json.loads(resp.read())
    print(f"5) dispatch API -> {dispatch_result}")

    time.sleep(3)  # allow ack -> in_progress -> completed round trip
    mission.refresh_from_db()
    print(f"6) mission status = {mission.status}")

    try:
        out, _ = proc.communicate(timeout=3)
    except subprocess.TimeoutExpired:
        proc.terminate()
        out, _ = proc.communicate(timeout=3)
    print("--- robot client output ---")
    print(out)
    print(f"--- robot client returncode = {proc.returncode} ---")

    ok = dispatch_result.get("success") and mission.status == ms.STATUS_COMPLETED
    print("=== E2E SMOKE: " + ("PASS" if ok else "FAIL") + " ===")

    # Cleanup test data.
    Mission.objects.filter(pk=mission.pk).delete()
    Order.objects.filter(pk=order.pk).delete()
    User.objects.filter(username="e2e_staff").delete()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
