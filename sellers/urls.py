from django.urls import path

from . import views

urlpatterns = [
    path("sellers/apply/", views.SellerApplyView.as_view(), name="seller-apply"),
    path("sellers/apply/status/", views.SellerApplicationStatusView.as_view(), name="seller-apply-status"),
    path("sellers/balance/", views.SellerBalanceView.as_view(), name="seller-balance"),
    path("sellers/payouts/", views.SellerPayoutListCreateView.as_view(), name="seller-payout-list"),
    path(
        "admin/sellers/applications/",
        views.AdminSellerApplicationListView.as_view(),
        name="admin-seller-application-list",
    ),
    path(
        "admin/sellers/applications/<int:pk>/review/",
        views.AdminSellerApplicationReviewView.as_view(),
        name="admin-seller-application-review",
    ),
]
