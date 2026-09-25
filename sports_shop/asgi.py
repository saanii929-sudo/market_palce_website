"""
ASGI config for sports_shop project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/6.1/howto/deployment/asgi/
"""

import os

import django
from django.core.asgi import get_asgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'sports_shop.settings')
django.setup()

django_asgi_app = get_asgi_application()

# Imported after django.setup() so chat.routing (and the models/serializers
# it pulls in) can safely import Django models at module load time.
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402

from chat.middleware import JWTAuthMiddleware  # noqa: E402
from chat.routing import websocket_urlpatterns as chat_websocket_urlpatterns  # noqa: E402
from riders.routing import websocket_urlpatterns as rider_websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        JWTAuthMiddleware(URLRouter(chat_websocket_urlpatterns + rider_websocket_urlpatterns))
    ),
})
