from django.urls import path

from . import views

urlpatterns = [
    path("cms/pages/<slug:slug>/", views.StaticPageDetailView.as_view(), name="static-page-detail"),
]
