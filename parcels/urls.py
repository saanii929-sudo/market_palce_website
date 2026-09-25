from django.urls import path

from . import views

urlpatterns = [
    path("parcels/quote/", views.ParcelQuoteView.as_view(), name="parcel-quote"),
    path("parcels/", views.ParcelListCreateView.as_view(), name="parcel-list-create"),
    path("parcels/payment-webhook/", views.ParcelPaymentWebhookView.as_view(), name="parcel-payment-webhook"),
    path("parcels/<int:pk>/checkout/", views.ParcelCheckoutView.as_view(), name="parcel-checkout"),
    path(
        "parcels/<int:pk>/checkout/status/",
        views.ParcelCheckoutStatusView.as_view(),
        name="parcel-checkout-status",
    ),
    path("parcels/<int:pk>/find-rider/", views.ParcelFindRiderView.as_view(), name="parcel-find-rider"),
    path("parcels/<int:pk>/tracking/", views.ParcelTrackingView.as_view(), name="parcel-tracking"),
    path("parcels/<int:pk>/rate-rider/", views.ParcelRateRiderView.as_view(), name="parcel-rate-rider"),
    path("parcels/<int:pk>/cancel/", views.ParcelCancelView.as_view(), name="parcel-cancel"),
]
