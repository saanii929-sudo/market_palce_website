from django.urls import path

from . import views

urlpatterns = [
    path("riders/register/", views.RiderRegisterView.as_view(), name="rider-register"),
    path("riders/documents/", views.RiderDocumentUploadView.as_view(), name="rider-document-upload"),
    path("riders/verification-status/", views.RiderVerificationStatusView.as_view(), name="rider-verification-status"),
    path("riders/vehicle/", views.RiderVehicleView.as_view(), name="rider-vehicle"),
    path("riders/me/settings/", views.RiderSettingsView.as_view(), name="rider-settings"),
    path("riders/online/", views.RiderOnlineToggleView.as_view(), name="rider-online-toggle"),
    path("riders/status/", views.RiderOnlineToggleView.as_view(), name="rider-status"),
    path("riders/location/", views.RiderLocationPingView.as_view(), name="rider-location-ping"),
    path("riders/offers/pending/", views.RiderPendingOffersView.as_view(), name="rider-offers-pending"),
    path("riders/offers/<int:pk>/accept/", views.RiderOfferAcceptView.as_view(), name="rider-offer-accept"),
    path("riders/offers/<int:pk>/decline/", views.RiderOfferDeclineView.as_view(), name="rider-offer-decline"),
    path(
        "riders/delivery-requests/<int:pk>/accept/",
        views.RiderDeliveryRequestAcceptView.as_view(), name="rider-delivery-request-accept",
    ),
    path(
        "riders/delivery-requests/<int:pk>/decline/",
        views.RiderDeliveryRequestDeclineView.as_view(), name="rider-delivery-request-decline",
    ),
    path("riders/deliveries/active/", views.RiderActiveDeliveryView.as_view(), name="rider-delivery-active"),
    path(
        "riders/deliveries/<int:pk>/confirm-pickup/",
        views.RiderDeliveryConfirmPickupView.as_view(), name="rider-delivery-confirm-pickup",
    ),
    path(
        "riders/deliveries/<int:pk>/complete/",
        views.RiderDeliveryCompleteView.as_view(), name="rider-delivery-complete",
    ),
    path("riders/deliveries/", views.RiderDeliveriesHistoryView.as_view(), name="rider-deliveries-history"),
    path("riders/earnings/summary/", views.RiderEarningsSummaryView.as_view(), name="rider-earnings-summary"),
    path(
        "riders/earnings/transactions/",
        views.RiderEarningsTransactionsView.as_view(),
        name="rider-earnings-transactions",
    ),
    path("riders/earnings/activity/", views.RiderEarningsActivityView.as_view(), name="rider-earnings-activity"),
    path("riders/payouts/", views.RiderPayoutRequestView.as_view(), name="rider-payout-request"),
    path(
        "riders/payout-methods/",
        views.RiderPayoutMethodListCreateView.as_view(), name="rider-payout-method-list",
    ),
    path(
        "riders/payout-methods/<int:pk>/set-default/",
        views.RiderPayoutMethodSetDefaultView.as_view(), name="rider-payout-method-set-default",
    ),
    path("riders/cash-out/", views.RiderCashOutView.as_view(), name="rider-cash-out"),
    path("riders/reviews/", views.RiderReviewsListView.as_view(), name="rider-reviews-list"),
    path("riders/reviews/summary/", views.RiderReviewsSummaryView.as_view(), name="rider-reviews-summary"),
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
