from django.urls import path

from . import views

urlpatterns = [
    path("cart/", views.CartView.as_view(), name="cart-detail"),
    path("cart/items/", views.CartItemListCreateView.as_view(), name="cart-item-add"),
    path("cart/items/<int:item_id>/", views.CartItemDetailView.as_view(), name="cart-item-detail"),
    path("cart/clear/", views.CartClearView.as_view(), name="cart-clear"),
    path("cart/coupon/", views.CouponApplyView.as_view(), name="cart-coupon"),
    path("promotions/", views.PromotionListView.as_view(), name="promotion-list"),
]
