import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meritlense.settings')

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

# get_asgi_application() runs django.setup(), populating the app registry -
# it must happen before anything that imports Django models transitively
# (both routing modules import consumers that import model classes). This
# previously "worked" only because nothing ever actually loaded this file in
# production - gunicorn was serving meritlense.wsgi:application, so asgi.py
# (and this ordering bug) was dormant. Switching to a Uvicorn worker for
# WebSocket support means this module is now genuinely imported, and Django
# raises AppRegistryNotReady if the routing imports happen first.
django_asgi_app = get_asgi_application()

from api.interviews.routing import websocket_urlpatterns
from api.live_calls.routing import websocket_urlpatterns as live_call_websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns + live_call_websocket_urlpatterns)
        ),
    }
)
