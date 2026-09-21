from django.urls import path

from . import views

urlpatterns = [
    path("delivery-methods/", views.DeliveryMethodListView.as_view(), name="delivery-method-list"),
    path("checkout/summary/", views.CheckoutSummaryView.as_view(), name="checkout-summary"),
    path("orders/", views.OrderListCreateView.as_view(), name="order-list"),
    path("orders/<str:order_number>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/tracking/", views.OrderTrackingView.as_view(), name="order-tracking"),
    path(
        "orders/<str:order_number>/seller-orders/<int:seller_order_id>/tracking/",
        views.SellerOrderTrackingView.as_view(),
        name="seller-order-tracking",
    ),
    path("orders/<str:order_number>/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<str:order_number>/buy-again/", views.OrderBuyAgainView.as_view(), name="order-buy-again"),
    path(
        "orders/<str:order_number>/items/<int:item_id>/refund-request/",
        views.RefundRequestCreateView.as_view(),
        name="refund-request-create",
    ),
    path("refund-requests/", views.RefundRequestListView.as_view(), name="refund-request-list"),
    path(
        "refund-requests/<int:refund_request_id>/status/",
        views.RefundRequestStatusUpdateView.as_view(),
        name="refund-request-status",
    ),
    path(
        "refund-requests/<int:refund_request_id>/escalate/",
        views.RefundRequestEscalateView.as_view(),
        name="refund-request-escalate",
    ),
    path("checkout/hubtel/status/", views.HubtelCheckoutStatusView.as_view(), name="hubtel-checkout-status"),
    path("payments/webhook/<str:gateway>/", views.PaymentWebhookView.as_view(), name="payment-webhook"),
]
