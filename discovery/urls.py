from django.urls import path

from . import views

urlpatterns = [
    path("discovery/home/", views.HomeView.as_view(), name="home"),
    path("discovery/search/suggest/", views.SearchSuggestView.as_view(), name="search-suggest"),
    path("discovery/search/recent/", views.SearchRecentView.as_view(), name="search-recent"),
    path("discovery/search/popular/", views.SearchPopularView.as_view(), name="search-popular"),
    path("discovery/search/log/", views.SearchLogView.as_view(), name="search-log"),
    path("discovery/recently-viewed/", views.RecentlyViewedListView.as_view(), name="recently-viewed"),
    path("newsletter/subscribe/", views.NewsletterSubscribeView.as_view(), name="newsletter-subscribe"),
]
