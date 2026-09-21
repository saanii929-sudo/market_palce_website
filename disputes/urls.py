from django.urls import path

from . import views

urlpatterns = [
    path("disputes/", views.DisputeListCreateView.as_view(), name="dispute-list"),
    path("disputes/<int:dispute_id>/messages/", views.DisputeMessageCreateView.as_view(), name="dispute-message-create"),
    path("disputes/<int:dispute_id>/resolve/", views.DisputeResolveView.as_view(), name="dispute-resolve"),
]
