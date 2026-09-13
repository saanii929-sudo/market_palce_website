from django.db import transaction
from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from reviews.models import Review
from reviews.serializers import ReviewSerializer

from .models import Category, Product
from .serializers import CategorySerializer, ProductDetailSerializer, ProductListSerializer

SORT_OPTIONS = {
    "price_asc": ["price"],
    "price_desc": ["-price"],
    "rating": ["-avg_rating"],
    "newest": ["-created_at"],
}


class CategoryListView(generics.ListAPIView):
    queryset = Category.objects.filter(is_active=True).prefetch_related("subcategories")
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class ProductListView(generics.ListAPIView):
    serializer_class = ProductListSerializer
    permission_classes = [permissions.AllowAny]

    def get_queryset(self):
        qs = Product.objects.filter(is_active=True).select_related("seller", "category", "brand").prefetch_related(
            "images"
        )
        params = self.request.query_params

        category = params.get("category")
        if category:
            qs = qs.filter(category__slug=category)

        subcategory = params.get("subcategory")
        if subcategory:
            qs = qs.filter(subcategory__slug=subcategory)

        brand = params.get("brand")
        if brand:
            qs = qs.filter(brand__slug=brand)

        seller = params.get("seller")
        if seller:
            qs = qs.filter(seller__slug=seller)

        sort = params.get("sort")
        ordering = SORT_OPTIONS.get(sort)
        if ordering:
            qs = qs.order_by(*ordering)

        return qs


class ProductDetailView(generics.RetrieveAPIView):
    queryset = Product.objects.filter(is_active=True)
    serializer_class = ProductDetailSerializer
    permission_classes = [permissions.AllowAny]
    lookup_field = "slug"


class ProductReviewListView(generics.ListAPIView):
    """Paginated reviews + rating histogram for a product. Creating a review
    now lives at POST /reviews/ (reviews app), which enforces verified
    purchase against a delivered OrderItem."""

    serializer_class = ReviewSerializer
    permission_classes = [permissions.AllowAny]

    def get_product(self):
        return get_object_or_404(Product, slug=self.kwargs["slug"], is_active=True)

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Review.objects.none()
        return Review.objects.filter(product=self.get_product()).select_related("user")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        histogram = {str(i): 0 for i in range(1, 6)}
        for row in queryset.values("rating").annotate(count=Count("id")):
            histogram[str(row["rating"])] = row["count"]

        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(page if page is not None else queryset, many=True)
        data = self.get_paginated_response(serializer.data).data if page is not None else {"results": serializer.data}

        product = self.get_product()
        data["avg_rating"] = product.avg_rating
        data["review_count"] = product.review_count
        data["histogram"] = histogram
        return Response(data)


class ProductViewTrackingView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(request=None, responses=None)
    def post(self, request, slug):
        product = get_object_or_404(Product, slug=slug, is_active=True)

        with transaction.atomic():
            Product.objects.filter(id=product.id).update(view_count=product.view_count + 1)

            if request.user.is_authenticated:
                from discovery.models import RecentlyViewed

                RecentlyViewed.objects.update_or_create(user=request.user, product=product)

        return Response(status=status.HTTP_204_NO_CONTENT)
