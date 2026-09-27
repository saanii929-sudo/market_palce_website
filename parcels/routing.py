from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"^ws/parcels/(?P<parcel_id>\d+)/tracking/$", consumers.ParcelTrackingConsumer.as_asgi()),
]
