from django.urls import path

from . import views

urlpatterns = [
    path("reviews/", views.ReviewCreateView.as_view(), name="review-create"),
    path("reviews/<int:pk>/", views.ReviewUpdateDeleteView.as_view(), name="review-detail"),
    path("reviews/<int:pk>/flag/", views.ReviewFlagView.as_view(), name="review-flag"),
    path("admin/reviews/flagged/", views.AdminFlaggedReviewListView.as_view(), name="admin-review-flagged-list"),
    path("admin/reviews/<int:pk>/moderate/", views.AdminReviewModerateView.as_view(), name="admin-review-moderate"),
]
