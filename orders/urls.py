from django.urls import path

from . import views

urlpatterns = [
    path("checkout/summary/", views.CheckoutSummaryView.as_view(), name="checkout-summary"),
    path("orders/", views.OrderListCreateView.as_view(), name="order-list"),
    path("orders/<str:order_number>/", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<str:order_number>/tracking/", views.OrderTrackingView.as_view(), name="order-tracking"),
    path("orders/<str:order_number>/cancel/", views.OrderCancelView.as_view(), name="order-cancel"),
    path("orders/<str:order_number>/buy-again/", views.OrderBuyAgainView.as_view(), name="order-buy-again"),
    path("checkout/hubtel/status/", views.HubtelCheckoutStatusView.as_view(), name="hubtel-checkout-status"),
    path("payments/webhook/<str:gateway>/", views.PaymentWebhookView.as_view(), name="payment-webhook"),
]
