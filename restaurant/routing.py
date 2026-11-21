from django.urls import re_path
from . import consumers
from . import robot_consumer

websocket_urlpatterns = [
    re_path(r"^ws/kitchen/$", consumers.KitchenConsumer.as_asgi()),
    re_path(
        r"^ws/robots/(?P<robot_id>[^/]+)/$",
        robot_consumer.RobotConsumer.as_asgi(),
    ),
]