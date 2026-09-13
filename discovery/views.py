from django.core.cache import cache
from django.db.models import Count
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from catalog.models import Brand, Category, Product, Seller
from catalog.serializers import (
    BannerSerializer,
    BrandSerializer,
    CollectionSerializer,
    FlashDealSerializer,
    ProductListSerializer,
    SellerSerializer,
)

from . import services
from .models import NewsletterSubscriber, RecentlyViewed, SearchQuery
from .serializers import (
    NewsletterSubscribeSerializer,
    RecentlyViewedSerializer,
    SearchLogSerializer,
    SearchQuerySerializer,
)

HOME_CACHE_TTL_SECONDS = 60
SEARCH_SUGGEST_LIMIT = 5
POPULAR_SEARCH_LIMIT = 10
POPULAR_SEARCH_CACHE_TTL_SECONDS = 300


class HomeView(APIView):
    """Single aggregated payload for the Home screen. Cached per
    (locale, is_flash_deal_window) since it's read-heavy and mostly identical
    across users."""

    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        locale = request.headers.get("Accept-Language", "en").split(",")[0].strip()
        flash_deal_window = services.has_live_flash_deal()
        cache_key = f"discovery:home:{locale}:{flash_deal_window}"

        payload = cache.get(cache_key)
        if payload is None:
            context = {"request": request}
            payload = {
                "banners": BannerSerializer(services.get_active_banners(), many=True, context=context).data,
                "flash_deals": FlashDealSerializer(
                    services.get_live_flash_deals(), many=True, context=context
                ).data,
                "popular_products": ProductListSerializer(
                    services.get_popular_products(), many=True, context=context
                ).data,
                "featured_products": ProductListSerializer(
                    services.get_featured_products(), many=True, context=context
                ).data,
                "trending_products": ProductListSerializer(
                    services.get_trending_products(), many=True, context=context
                ).data,
                "collections": CollectionSerializer(
                    services.get_active_collections(), many=True, context=context
                ).data,
                "brands": BrandSerializer(services.get_brands(), many=True, context=context).data,
                "featured_sellers": SellerSerializer(
                    services.get_featured_sellers(), many=True, context=context
                ).data,
            }
            cache.set(cache_key, payload, HOME_CACHE_TTL_SECONDS)

        # Recommendations are per-user, so they're computed fresh and layered
        # on top of the shared cached payload rather than baked into its key.
        response_data = dict(payload)
        response_data["recommended_products"] = ProductListSerializer(
            services.get_recommended_products(request.user if request.user.is_authenticated else None),
            many=True,
            context={"request": request},
        ).data
        return Response(response_data)


class SearchSuggestView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        query = request.query_params.get("q", "").strip()
        if not query:
            return Response({"products": [], "brands": [], "categories": [], "sellers": []})

        products = Product.objects.filter(is_active=True, name__icontains=query)[:SEARCH_SUGGEST_LIMIT]
        brands = Brand.objects.filter(is_active=True, name__icontains=query)[:SEARCH_SUGGEST_LIMIT]
        categories = Category.objects.filter(is_active=True, name__icontains=query)[:SEARCH_SUGGEST_LIMIT]
        sellers = Seller.objects.filter(business_name__icontains=query)[:SEARCH_SUGGEST_LIMIT]

        context = {"request": request}
        return Response(
            {
                "products": ProductListSerializer(products, many=True, context=context).data,
                "brands": BrandSerializer(brands, many=True, context=context).data,
                "categories": [{"id": c.id, "name": c.name, "slug": c.slug} for c in categories],
                "sellers": SellerSerializer(sellers, many=True, context=context).data,
            }
        )


class SearchRecentView(generics.ListAPIView):
    serializer_class = SearchQuerySerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return SearchQuery.objects.none()

        # SQLite (dev default) doesn't support distinct(field), so
        # de-duplicate by query_text in Python instead.
        seen = set()
        results = []
        for query in SearchQuery.objects.filter(user=self.request.user).order_by("-created_at"):
            key = query.query_text.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(query)
            if len(results) >= 10:
                break
        return results


class SearchPopularView(APIView):
    permission_classes = [permissions.AllowAny]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        cache_key = "discovery:search:popular"
        data = cache.get(cache_key)
        if data is None:
            top_queries = (
                SearchQuery.objects.values("query_text")
                .annotate(count=Count("id"))
                .order_by("-count")[:POPULAR_SEARCH_LIMIT]
            )
            data = [row["query_text"] for row in top_queries]
            cache.set(cache_key, data, POPULAR_SEARCH_CACHE_TTL_SECONDS)
        return Response({"results": data})


class SearchLogView(APIView):
    serializer_class = SearchLogSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = SearchLogSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        SearchQuery.objects.create(
            user=request.user if request.user.is_authenticated else None,
            query_text=serializer.validated_data["query_text"],
        )
        return Response(status=status.HTTP_201_CREATED)


class RecentlyViewedListView(generics.ListAPIView):
    serializer_class = RecentlyViewedSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return RecentlyViewed.objects.none()
        return (
            RecentlyViewed.objects.filter(user=self.request.user)
            .select_related("product")
            .order_by("-viewed_at")
        )


class NewsletterSubscribeView(APIView):
    serializer_class = NewsletterSubscribeSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = NewsletterSubscribeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        NewsletterSubscriber.objects.get_or_create(email=serializer.validated_data["email"])
        return Response({"detail": "Subscribed."}, status=status.HTTP_201_CREATED)
