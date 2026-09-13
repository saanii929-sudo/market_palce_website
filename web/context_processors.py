from cart.models import Cart
from notifications.models import Notification
from wishlist import services as wishlist_services


def site_chrome(request):
    cart_count = 0
    if request.user.is_authenticated:
        cart = Cart.objects.filter(user=request.user).first()
    else:
        session_key = getattr(request.session, "session_key", None)
        cart = Cart.objects.filter(session_key=session_key).first() if session_key else None
    if cart:
        cart_count = sum(cart.items.values_list("qty", flat=True))

    unread_notifications = 0
    if request.user.is_authenticated:
        unread_notifications = Notification.objects.filter(user=request.user, is_read=False).count()

    return {
        "cart_count": cart_count,
        "wishlist_count": wishlist_services.count(request),
        "unread_notifications": unread_notifications,
    }
