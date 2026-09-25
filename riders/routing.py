from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/riders/dispatch/$", consumers.RiderDispatchConsumer.as_asgi()),
]
