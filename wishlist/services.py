from catalog.models import Product

from .models import WishlistItem


def _owner_kwargs_for_write(request) -> dict:
    if request.user.is_authenticated:
        return {"user": request.user, "session_key": None}
    if not request.session.session_key:
        request.session.save()
    return {"user": None, "session_key": request.session.session_key}


def _owner_kwargs_for_read(request) -> dict | None:
    if request.user.is_authenticated:
        return {"user": request.user}
    session_key = getattr(request.session, "session_key", None)
    if not session_key:
        return None
    return {"session_key": session_key}


def list_items(request):
    owner_kwargs = _owner_kwargs_for_read(request)
    if owner_kwargs is None:
        return WishlistItem.objects.none()
    return WishlistItem.objects.filter(**owner_kwargs).select_related("product", "product__brand", "product__seller")


def get_wishlisted_product_ids(request) -> set[int]:
    owner_kwargs = _owner_kwargs_for_read(request)
    if owner_kwargs is None:
        return set()
    return set(WishlistItem.objects.filter(**owner_kwargs).values_list("product_id", flat=True))


def count(request) -> int:
    owner_kwargs = _owner_kwargs_for_read(request)
    if owner_kwargs is None:
        return 0
    return WishlistItem.objects.filter(**owner_kwargs).count()


def remove(request, product: Product) -> None:
    owner_kwargs = _owner_kwargs_for_write(request)
    WishlistItem.objects.filter(product=product, **owner_kwargs).delete()


def toggle(request, product: Product) -> bool:
    owner_kwargs = _owner_kwargs_for_write(request)
    existing = WishlistItem.objects.filter(product=product, **owner_kwargs).first()
    if existing:
        existing.delete()
        return False
    WishlistItem.objects.create(product=product, **owner_kwargs)
    return True


def merge_guest_wishlist_into_user_wishlist(session_key: str, user) -> None:
    guest_items = WishlistItem.objects.filter(session_key=session_key)
    if not guest_items.exists():
        return

    existing_product_ids = set(WishlistItem.objects.filter(user=user).values_list("product_id", flat=True))
    for item in guest_items:
        if item.product_id in existing_product_ids:
            item.delete()
        else:
            item.user = user
            item.session_key = None
            item.save(update_fields=["user", "session_key"])
