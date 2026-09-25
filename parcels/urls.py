from django.urls import path

from . import views

urlpatterns = [
    path("parcels/quote/", views.ParcelQuoteView.as_view(), name="parcel-quote"),
    path("parcels/", views.ParcelListCreateView.as_view(), name="parcel-list-create"),
    path("parcels/<int:pk>/tracking/", views.ParcelTrackingView.as_view(), name="parcel-tracking"),
    path("parcels/<int:pk>/cancel/", views.ParcelCancelView.as_view(), name="parcel-cancel"),
]
