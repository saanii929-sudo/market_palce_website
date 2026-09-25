from django.urls import path

from . import views

urlpatterns = [
    path("riders/register/", views.RiderRegisterView.as_view(), name="rider-register"),
    path("riders/documents/", views.RiderDocumentUploadView.as_view(), name="rider-document-upload"),
    path("riders/verification-status/", views.RiderVerificationStatusView.as_view(), name="rider-verification-status"),
    path("riders/vehicle/", views.RiderVehicleView.as_view(), name="rider-vehicle"),
    path("riders/me/settings/", views.RiderSettingsView.as_view(), name="rider-settings"),
    path("riders/online/", views.RiderOnlineToggleView.as_view(), name="rider-online-toggle"),
    path("riders/location/", views.RiderLocationPingView.as_view(), name="rider-location-ping"),
    path("riders/offers/pending/", views.RiderPendingOffersView.as_view(), name="rider-offers-pending"),
    path("riders/offers/<int:pk>/accept/", views.RiderOfferAcceptView.as_view(), name="rider-offer-accept"),
    path("riders/offers/<int:pk>/decline/", views.RiderOfferDeclineView.as_view(), name="rider-offer-decline"),
    path("riders/earnings/summary/", views.RiderEarningsSummaryView.as_view(), name="rider-earnings-summary"),
    path(
        "riders/earnings/transactions/",
        views.RiderEarningsTransactionsView.as_view(),
        name="rider-earnings-transactions",
    ),
    path("riders/payouts/", views.RiderPayoutRequestView.as_view(), name="rider-payout-request"),
    path(
        "admin/riders/verification-queue/",
        views.AdminRiderVerificationQueueView.as_view(),
        name="admin-rider-verification-queue",
    ),
    path(
        "admin/riders/documents/<int:pk>/review/",
        views.AdminRiderDocumentReviewView.as_view(),
        name="admin-rider-document-review",
    ),
]
