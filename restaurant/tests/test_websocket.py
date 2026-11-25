"""
تست‌های WebSocket / Daphne / Channels برای لایه ربات (robot_consumer).

پوشش:
- اتصال با توکن معتبر -> پذیرش (اتصال برقرار)
- اتصال بدون توکن / توکن نامعتبر -> رد شدن (fail-closed)
- ارسال/دریافت پیام ساده (ping -> pong) از طریق consumer
- بارگذاری اپلیکیشن ASGI (قابل اجرا توسط Daphne) با پشتیبانی HTTP و WebSocket
"""

import pytest
from django.test import override_settings
from channels.testing import WebsocketCommunicator

ROBOT_PATH = "/ws/robots/RBOT-01/"
VALID_TOKEN = "test-robot-token"


def _asgi_application():
    """اپلیکیشن ASGI پروژه (Daphne از آن اجرا می‌شود)."""
    from RestaurantRobot.asgi import application
    return application


@pytest.mark.asyncio
async def test_websocket_connect_with_valid_token():
    """اتصال با توکن معتبر باید پذیرفته شود."""
    from RestaurantRobot.asgi import application
    with override_settings(ROBOT_API_TOKEN=VALID_TOKEN):
        communicator = WebsocketCommunicator(
            application, ROBOT_PATH + "?token=" + VALID_TOKEN
        )
        connected, _ = await communicator.connect()
        assert connected is True
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_connect_without_token_rejected():
    """اتصال بدون توکن باید رد شود (fail-closed)."""
    from RestaurantRobot.asgi import application
    with override_settings(ROBOT_API_TOKEN=VALID_TOKEN):
        communicator = WebsocketCommunicator(application, ROBOT_PATH)
        connected, _ = await communicator.connect()
        assert connected is False
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_connect_with_invalid_token_rejected():
    """اتصال با توکن نامعتبر باید رد شود (fail-closed)."""
    from RestaurantRobot.asgi import application
    with override_settings(ROBOT_API_TOKEN=VALID_TOKEN):
        communicator = WebsocketCommunicator(
            application, ROBOT_PATH + "?token=wrong-token"
        )
        connected, _ = await communicator.connect()
        assert connected is False
        await communicator.disconnect()


@pytest.mark.asyncio
async def test_websocket_send_receive_ping():
    """پیام ping ساده باید پاسخ pong از consumer بگیرد."""
    from RestaurantRobot.asgi import application
    with override_settings(ROBOT_API_TOKEN=VALID_TOKEN):
        communicator = WebsocketCommunicator(
            application, ROBOT_PATH + "?token=" + VALID_TOKEN
        )
        connected, _ = await communicator.connect()
        assert connected is True
        # پیام خوش‌آمد اولیه که consumer پس از accept می‌فرستد.
        greeting = await communicator.receive_json_from()
        assert greeting.get("event") == "connected"
        await communicator.send_json_to({"event": "ping"})
        response = await communicator.receive_json_from()
        assert response.get("event") == "pong"
        await communicator.disconnect()


def test_asgi_application_loads_for_daphne():
    """اپلیکیشن ASGI بارگذاری می‌شود و هم HTTP و هم WebSocket را پشتیبانی می‌کند."""
    from channels.routing import ProtocolTypeRouter
    application = _asgi_application()
    assert isinstance(application, ProtocolTypeRouter)
    assert "http" in application.application_mapping
    assert "websocket" in application.application_mapping
