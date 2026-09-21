from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.NotificationListView.as_view(), name="notification-list"),
    path("notifications/read-all/", views.NotificationMarkAllReadView.as_view(), name="notification-read-all"),
    path("notifications/<int:pk>/read/", views.NotificationMarkReadView.as_view(), name="notification-read"),
    path("notifications/device-tokens/", views.DeviceTokenView.as_view(), name="notification-device-token"),
    path("admin/broadcasts/", views.AdminBroadcastListCreateView.as_view(), name="admin-broadcast-list"),
]
