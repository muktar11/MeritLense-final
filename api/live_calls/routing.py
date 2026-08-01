from django.urls import re_path

from .consumers import LiveCallConsumer

websocket_urlpatterns = [
    re_path(r"^ws/live-calls/(?P<call_id>[0-9a-f-]+)/$", LiveCallConsumer.as_asgi()),
]

