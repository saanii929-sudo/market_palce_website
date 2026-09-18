from django.urls import path

from . import views

urlpatterns = [
    path("catalog/categories/", views.CategoryListView.as_view(), name="catalog-category-list"),
    path("catalog/categories/<slug:slug>/products/", views.CategoryProductListView.as_view(), name="category-product-list"),
    path("catalog/products/", views.ProductListView.as_view(), name="product-list"),
    path("catalog/products/<slug:slug>/", views.ProductDetailView.as_view(), name="product-detail"),
    path("catalog/products/<slug:slug>/reviews/", views.ProductReviewListView.as_view(), name="product-reviews"),
    path("catalog/products/<slug:slug>/view/", views.ProductViewTrackingView.as_view(), name="product-track-view"),
    path("catalog/sellers/<slug:slug>/", views.SellerDetailView.as_view(), name="seller-detail"),
    path("catalog/brands/<slug:slug>/", views.BrandDetailView.as_view(), name="brand-detail"),
]
