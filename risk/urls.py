from django.urls import path

from . import views

urlpatterns = [
    path("admin/risk-flags/", views.RiskFlagListView.as_view(), name="risk-flag-list"),
    path("admin/risk-flags/<int:risk_flag_id>/", views.RiskFlagReviewView.as_view(), name="risk-flag-review"),
]
