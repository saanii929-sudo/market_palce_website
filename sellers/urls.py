from django.urls import path

from . import views

urlpatterns = [
    path("sellers/apply/", views.SellerApplyView.as_view(), name="seller-apply"),
    path("sellers/apply/status/", views.SellerApplicationStatusView.as_view(), name="seller-apply-status"),
    path("sellers/apply/kyc/", views.SellerKYCSubmitView.as_view(), name="seller-apply-kyc"),
    path("sellers/balance/", views.SellerBalanceView.as_view(), name="seller-balance"),
    path("sellers/payouts/", views.SellerPayoutListCreateView.as_view(), name="seller-payout-list"),
    path("seller/coupons/", views.SellerCouponListCreateView.as_view(), name="seller-coupon-list"),
    path(
        "seller/orders/<int:suborder_id>/nearby-riders/",
        views.SellerNearbyRidersView.as_view(),
        name="seller-order-nearby-riders",
    ),
    path(
        "seller/orders/<int:suborder_id>/request-rider/",
        views.SellerRequestRiderView.as_view(),
        name="seller-order-request-rider",
    ),
    path(
        "seller/orders/<int:suborder_id>/delivery-status/",
        views.SellerOrderDeliveryStatusView.as_view(),
        name="seller-order-delivery-status",
    ),
    path(
        "seller/favorite-riders/",
        views.SellerFavoriteRiderListCreateView.as_view(),
        name="seller-favorite-rider-list",
    ),
    path(
        "seller/favorite-riders/<int:rider_id>/",
        views.SellerFavoriteRiderDeleteView.as_view(),
        name="seller-favorite-rider-delete",
    ),
    path("seller/riders/<int:rider_id>/block/", views.SellerRiderBlockView.as_view(), name="seller-rider-block"),
    path(
        "seller/products/bulk-upload/",
        views.SellerBulkUploadCreateView.as_view(),
        name="seller-bulk-upload-create",
    ),
    path(
        "seller/products/bulk-upload/<int:job_id>/",
        views.SellerBulkUploadDetailView.as_view(),
        name="seller-bulk-upload-detail",
    ),
    path(
        "sellers/subscriptions/webhook/",
        views.SubscriptionWebhookView.as_view(),
        name="seller-subscription-webhook",
    ),
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
    path("admin/sellers/kyc-queue/", views.AdminSellerKYCQueueView.as_view(), name="admin-seller-kyc-queue"),
    path("admin/sellers/<int:pk>/kyc/verify/", views.AdminSellerKYCVerifyView.as_view(), name="admin-seller-kyc-verify"),
    path("admin/sellers/<int:pk>/kyc/reject/", views.AdminSellerKYCRejectView.as_view(), name="admin-seller-kyc-reject"),
]
