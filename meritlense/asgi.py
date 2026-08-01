import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from api.interviews.routing import websocket_urlpatterns
from api.live_calls.routing import websocket_urlpatterns as live_call_websocket_urlpatterns

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meritlense.settings')

django_asgi_app = get_asgi_application()
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns + live_call_websocket_urlpatterns)
        ),
    }
)
