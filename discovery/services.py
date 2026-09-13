from django.db.models import Count
from django.utils import timezone

from catalog.models import Banner, Brand, Collection, FlashDeal, Product, Seller

from .models import RecentlyViewed

RECOMMENDED_LIMIT = 12
TRENDING_LIMIT = 12
POPULAR_LIMIT = 12
FEATURED_LIMIT = 12


def get_active_banners():
    now = timezone.now()
    return (
        Banner.objects.filter(is_active=True)
        .exclude(active_from__gt=now)
        .exclude(active_to__lt=now)
        .order_by("display_order")
    )


def get_live_flash_deals():
    now = timezone.now()
    return (
        FlashDeal.objects.filter(is_active=True, starts_at__lte=now, ends_at__gte=now, stock_qty__gt=0)
        .select_related("product")
        .order_by("ends_at")
    )


def has_live_flash_deal() -> bool:
    now = timezone.now()
    return FlashDeal.objects.filter(
        is_active=True, starts_at__lte=now, ends_at__gte=now, stock_qty__gt=0
    ).exists()


def get_popular_products(limit=POPULAR_LIMIT):
    """Highest total sold_count. A trailing-window version needs a
    timestamped sales ledger, which doesn't exist until the orders app lands."""
    return Product.objects.filter(is_active=True).order_by("-sold_count")[:limit]


def get_featured_products(limit=FEATURED_LIMIT):
    return Product.objects.filter(is_active=True, is_featured=True).order_by("-created_at")[:limit]


def get_trending_products(limit=TRENDING_LIMIT):
    """Approximates sale/view velocity using RecentlyViewed timestamps -
    the only per-event, timestamped signal available pre-orders-app.
    Ranks products by (views in the last 7 days) - (views in the 7 days before that).
    """
    now = timezone.now()
    recent_start = now - timezone.timedelta(days=7)
    prior_start = now - timezone.timedelta(days=14)

    recent_counts = dict(
        RecentlyViewed.objects.filter(viewed_at__gte=recent_start)
        .values_list("product_id")
        .annotate(c=Count("id"))
    )
    prior_counts = dict(
        RecentlyViewed.objects.filter(viewed_at__gte=prior_start, viewed_at__lt=recent_start)
        .values_list("product_id")
        .annotate(c=Count("id"))
    )

    product_ids = set(recent_counts) | set(prior_counts)
    ranked = sorted(
        product_ids, key=lambda pid: recent_counts.get(pid, 0) - prior_counts.get(pid, 0), reverse=True
    )[:limit]

    products = Product.objects.filter(id__in=ranked, is_active=True)
    products_by_id = {p.id: p for p in products}
    return [products_by_id[pid] for pid in ranked if pid in products_by_id]


def get_recommended_products(user, limit=RECOMMENDED_LIMIT):
    """Same categories as the user's interests + recently viewed products.
    Falls back to popular products for anonymous users or users with no signal."""
    if user is None or not user.is_authenticated:
        return get_popular_products(limit)

    interest_category_ids = set(user.interests.values_list("id", flat=True))
    viewed_product_ids = set(
        RecentlyViewed.objects.filter(user=user).values_list("product_id", flat=True)
    )
    viewed_category_ids = set(
        Product.objects.filter(id__in=viewed_product_ids).values_list("category_id", flat=True)
    )
    category_ids = interest_category_ids | viewed_category_ids

    if not category_ids:
        return get_popular_products(limit)

    return (
        Product.objects.filter(is_active=True, category_id__in=category_ids)
        .exclude(id__in=viewed_product_ids)
        .order_by("-avg_rating", "-sold_count")[:limit]
    )


def get_active_collections():
    return Collection.objects.filter(is_active=True).prefetch_related("products").order_by("display_order")


def get_brands(limit=20):
    return Brand.objects.filter(is_active=True).order_by("name")[:limit]


def get_featured_sellers(limit=10):
    return Seller.objects.filter(is_featured=True).order_by("-rating")[:limit]
