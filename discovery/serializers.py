from rest_framework import serializers

from catalog.serializers import ProductListSerializer

from .models import RecentlyViewed, SearchQuery


class RecentlyViewedSerializer(serializers.ModelSerializer):
    product = ProductListSerializer(read_only=True)

    class Meta:
        model = RecentlyViewed
        fields = ["product", "viewed_at"]


class SearchQuerySerializer(serializers.ModelSerializer):
    class Meta:
        model = SearchQuery
        fields = ["id", "query_text", "created_at"]
        read_only_fields = ["id", "created_at"]


class SearchLogSerializer(serializers.Serializer):
    query_text = serializers.CharField(max_length=255)


class NewsletterSubscribeSerializer(serializers.Serializer):
    """Plain Serializer (not ModelSerializer) so resubscribing an existing
    email isn't rejected by DRF's automatic UniqueValidator - see
    NewsletterSubscribeView, which upserts instead."""

    email = serializers.EmailField()
