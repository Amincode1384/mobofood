"""
ASGI config for RestaurantRobot project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'RestaurantRobot.settings')

# django_asgi_app must be created BEFORE importing anything that touches
# models/consumers, so Django's app registry is fully populated first.
django_asgi_app = get_asgi_application()

from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from restaurant.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    # AuthMiddlewareStack لازم است تا scope["user"] برای کنترل دسترسی
    # کانال زندهٔ آشپزخانه پر شود.
    "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
})