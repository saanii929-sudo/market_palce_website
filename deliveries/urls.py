from django.urls import path

from . import views

urlpatterns = [
    path("deliveries/<int:pk>/dispatch/", views.DeliveryDispatchView.as_view(), name="delivery-dispatch"),
    path("deliveries/<int:pk>/tracking/", views.DeliveryTrackingView.as_view(), name="delivery-tracking"),
    path("trips/<int:pk>/confirm-pickup/", views.TripConfirmPickupView.as_view(), name="trip-confirm-pickup"),
    path("trips/<int:pk>/proof-of-delivery/", views.TripProofOfDeliveryView.as_view(), name="trip-proof-of-delivery"),
    path("trips/<int:pk>/complete/", views.TripCompleteView.as_view(), name="trip-complete"),
    path("trips/<int:pk>/rate-rider/", views.TripRateRiderView.as_view(), name="trip-rate-rider"),
    path("trips/<int:pk>/rate-seller/", views.TripRateSellerView.as_view(), name="trip-rate-seller"),
]
