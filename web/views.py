import secrets
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.models import ProtectedError, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import (
    url_has_allowed_host_and_scheme,
    urlsafe_base64_decode,
    urlsafe_base64_encode,
)
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.models import Address, OTPCode, User
from accounts.serializers import AddressSerializer, RegisterSerializer
from accounts.services.otp import OTPVerificationError, send_otp, verify_otp
from cart import services as cart_services
from cart.models import CartItem
from catalog.models import Brand, Category, Collection, Product, ProductImage, ProductVariant, Seller
from core.defaults import CannotDeleteOnlyDefaultError, handle_deletion
from core.sms import get_sms_backend
from discovery import services as discovery_services
from notifications.models import Notification
from orders.models import DeliveryMethod, Order, OrderItem, PaymentMethod, PendingCheckout, ReturnRequest, Shipment
from orders.services.hubtel_checkout import (
    HubtelCheckoutError,
    finalize_pending_checkout,
    mark_pending_checkout_failed,
    start_hubtel_checkout,
)
from orders.services.order_placement import OrderPlacementError, place_order
from orders.services.payment_gateway import HUBTEL_PAYMENT_METHOD_CODES, HubtelGateway, PaymentGatewayError
from orders.services.returns import ReturnError, eligible_order_items, request_return, resolve_return_request
from payments.models import PaymentMethodToken
from payments.serializers import PaymentMethodTokenCreateSerializer
from reviews.models import Review
from sellers.models import Payout, SellerApplication
from sellers.services import PayoutError, get_available_balance, request_withdrawal
from support.models import FAQ
from support.serializers import SupportTicketCreateSerializer
from wishlist import services as wishlist_services

from cms.models import StaticPage

RESET_TOKEN_GENERATOR = PasswordResetTokenGenerator()
WEB_AUTH_BACKEND = "accounts.backends.EmailOrPhoneBackend"
PRODUCTS_PER_PAGE = 12
CARD_BRANDS = {"visa", "mastercard", "amex", "verve"}


def _safe_redirect_target(request, candidate: str | None, default: str) -> str:
    if candidate and url_has_allowed_host_and_scheme(
        candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return candidate
    return default


def _orders_count(request) -> int:
    if not request.user.is_authenticated:
        return 0
    return Order.objects.filter(user=request.user).count()


def customer_account_required(view_func):
    """Like @login_required, but a logged-in seller or superadmin gets bounced
    to their own dashboard instead of seeing the customer account pages."""

    @wraps(view_func)
    @login_required(login_url="web-login")
    def wrapper(request, *args, **kwargs):
        if request.user.is_superuser:
            messages.info(request, "You're logged in as an admin — here's the admin console.")
            return redirect("web-console-overview")
        if getattr(request.user, "seller_profile", None) is not None:
            messages.info(request, "You're logged in as a seller — here's your store dashboard.")
            return redirect("web-seller-overview")
        return view_func(request, *args, **kwargs)

    return wrapper


def _post_login_redirect_target(user) -> str:
    if user.is_superuser:
        return "web-console-overview"
    if getattr(user, "seller_profile", None) is not None:
        return "web-seller-overview"
    return "web-home"


def login_view(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect_target(request.user))

    if request.method == "POST":
        channel = request.POST.get("channel", "email")
        raw_identifier = request.POST.get("identifier") if channel == "email" else request.POST.get("identifier_phone")
        identifier = (raw_identifier or "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=identifier, password=password) if identifier and password else None
        if user is None:
            return render(request, "web/login.html", {
                "errors": "Invalid credentials.", "channel": channel, "identifier": identifier,
            })
        if not user.is_verified:
            return render(request, "web/login.html", {
                "errors": "Please verify your account before logging in.", "channel": channel, "identifier": identifier,
            })

        if request.session.session_key:
            cart_services.merge_guest_cart_into_user_cart(request.session.session_key, user)
            wishlist_services.merge_guest_wishlist_into_user_wishlist(request.session.session_key, user)
            discovery_services.merge_guest_recently_viewed_into_user(request.session.session_key, user)

        django_login(request, user)
        messages.success(request, f"Welcome back, {user.full_name or user.email or user.phone}!")
        return redirect(_post_login_redirect_target(user))

    return render(request, "web/login.html", {"channel": "email"})


def logout_view(request):
    django_logout(request)
    return redirect("web-home")


def register_view(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect_target(request.user))

    if request.method == "POST":
        channel = request.POST.get("channel", "email")
        full_name = request.POST.get("full_name", "").strip()
        email = request.POST.get("email", "").strip()
        phone = request.POST.get("phone", "").strip()
        password = request.POST.get("password", "")
        confirm_password = request.POST.get("confirm_password", "")
        agree_terms = request.POST.get("agree_terms")

        errors = []
        if not agree_terms:
            errors.append("You must agree to the Terms of Service and Privacy Policy.")
        if password != confirm_password:
            errors.append("Passwords do not match.")

        payload = {"full_name": full_name, "password": password}
        payload["phone"] = phone if channel == "phone" else None
        payload["email"] = email if channel == "email" else None

        serializer = RegisterSerializer(data=payload)
        if not serializer.is_valid():
            for field_errors in serializer.errors.values():
                errors.extend(str(e) for e in field_errors)

        if errors:
            return render(request, "web/register.html", {
                "errors": errors, "channel": channel, "full_name": full_name, "email": email, "phone": phone,
            })

        user = serializer.save()
        destination = user.email or user.phone
        send_otp(destination, OTPCode.Purpose.SIGNUP_VERIFY, user=user)

        request.session["pending_verification"] = {"destination": destination, "purpose": OTPCode.Purpose.SIGNUP_VERIFY}
        return redirect("web-verify")

    return render(request, "web/register.html", {"channel": "email"})


def verify_view(request):
    pending = request.session.get("pending_verification")
    if not pending:
        return redirect("web-register")

    if request.method == "POST":
        if request.POST.get("action") == "resend":
            send_otp(pending["destination"], pending["purpose"])
            messages.success(request, "We've sent a new code.")
            return render(request, "web/verify.html", {"destination": pending["destination"]})

        code = request.POST.get("code", "")
        try:
            otp = verify_otp(pending["destination"], pending["purpose"], code, consume=True)
        except OTPVerificationError as exc:
            return render(request, "web/verify.html", {"destination": pending["destination"], "errors": exc.message})

        user = otp.user
        channel_field = "is_email_verified" if otp.channel == OTPCode.Channel.EMAIL else "is_phone_verified"
        setattr(user, channel_field, True)
        user.save(update_fields=[channel_field])

        del request.session["pending_verification"]
        user.backend = WEB_AUTH_BACKEND
        django_login(request, user)
        messages.success(request, "Your account is verified. Welcome to SportTech!")
        return redirect("web-home")

    return render(request, "web/verify.html", {"destination": pending["destination"]})


def forgot_password_view(request):
    if request.method == "POST":
        channel = request.POST.get("channel", "email")
        raw_identifier = request.POST.get("identifier") if channel == "email" else request.POST.get("identifier_phone")
        identifier = (raw_identifier or "").strip()

        user = User.objects.filter(Q(email__iexact=identifier) | Q(phone=identifier)).first()
        if user is not None:
            token = RESET_TOKEN_GENERATOR.make_token(user)
            uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
            link = request.build_absolute_uri(reverse("web-reset-password", args=[uidb64, token]))
            message = f"Reset your SportTech password: {link}\nThis link expires in 30 minutes."
            if user.email:
                send_mail("Reset your SportTech password", message, settings.DEFAULT_FROM_EMAIL, [user.email], using="default")
            elif user.phone:
                get_sms_backend().send(user.phone, message)

        return render(request, "web/forgot_password.html", {"sent": True, "destination": identifier})

    return render(request, "web/forgot_password.html", {"channel": "email"})


def reset_password_view(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        user = None

    token_valid = user is not None and RESET_TOKEN_GENERATOR.check_token(user, token)
    if not token_valid:
        return render(request, "web/reset_password.html", {"invalid_link": True})

    if request.method == "POST":
        new_password = request.POST.get("new_password", "")
        confirm_password = request.POST.get("confirm_password", "")

        errors = []
        if new_password != confirm_password:
            errors.append("Passwords do not match.")
        else:
            try:
                validate_password(new_password, user=user)
            except DjangoValidationError as exc:
                errors.extend(exc.messages)

        if errors:
            return render(request, "web/reset_password.html", {"errors": errors})

        user.set_password(new_password)
        user.save(update_fields=["password"])

        for outstanding in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=outstanding)

        return redirect("web-password-updated")

    return render(request, "web/reset_password.html", {})


def password_updated_view(request):
    return render(request, "web/password_updated.html", {})


def social_login_view(request, provider):
    messages.info(request, f"{provider.title()} sign-in isn't configured in this environment yet.")
    return redirect("web-login")

def home(request):
    trending = list(discovery_services.get_trending_products())
    if not trending:
        trending = list(discovery_services.get_popular_products())

    context = {
        "categories": Category.objects.filter(is_active=True)[:10],
        "banners": list(discovery_services.get_active_banners()),
        "flash_deals": list(discovery_services.get_live_flash_deals()),
        "trending_products": trending,
        "collections": discovery_services.get_active_collections(),
        "brands": discovery_services.get_brands(),
        "wishlisted_ids": wishlist_services.get_wishlisted_product_ids(request),
    }
    return render(request, "web/home.html", context)


SORT_OPTIONS = {
    "popularity": "-sold_count",
    "price_asc": "price",
    "price_desc": "-price",
    "rating": "-avg_rating",
    "newest": "-created_at",
}
SORT_LABELS = {
    "popularity": "Popularity", "price_asc": "Price: Low to High",
    "price_desc": "Price: High to Low", "rating": "Rating", "newest": "Newest",
}


def _render_product_list(request, base_qs, *, title, breadcrumbs, facet_param="category", facet_options=None, default_sort="popularity"):
    
    base_qs = base_qs.select_related("brand", "seller", "category")

    available_brands = Brand.objects.filter(is_active=True, id__in=base_qs.values_list("brand_id", flat=True).distinct()).order_by("name")

    products = base_qs
    selected_facets = request.GET.getlist(facet_param)
    if selected_facets:
        products = products.filter(**{f"{facet_param}__slug__in": selected_facets})

    selected_brands = request.GET.getlist("brand")
    if selected_brands:
        products = products.filter(brand__slug__in=selected_brands)

    min_price = request.GET.get("min_price", "").strip()
    max_price = request.GET.get("max_price", "").strip()
    if min_price:
        products = products.filter(price__gte=min_price)
    if max_price:
        products = products.filter(price__lte=max_price)

    min_rating = request.GET.get("min_rating", "").strip()
    if min_rating:
        products = products.filter(avg_rating__gte=min_rating)

    sort = request.GET.get("sort", default_sort)
    products = products.order_by(SORT_OPTIONS.get(sort, SORT_OPTIONS[default_sort]))

    paginator = Paginator(products, PRODUCTS_PER_PAGE)
    page_obj = paginator.get_page(request.GET.get("page"))

    querydict = request.GET.copy()
    querydict.pop("page", None)

    return render(request, "web/product_list.html", {
        "title": title,
        "breadcrumbs": breadcrumbs,
        "page_obj": page_obj,
        "result_count": paginator.count,
        "facet_param": facet_param,
        "facet_options": facet_options or [],
        "selected_facets": selected_facets,
        "available_brands": available_brands,
        "selected_brands": selected_brands,
        "min_price": min_price,
        "max_price": max_price,
        "min_rating": min_rating,
        "sort": sort,
        "sort_options": [(k, v) for k, v in SORT_LABELS.items()],
        "base_querystring": querydict.urlencode(),
        "wishlisted_ids": wishlist_services.get_wishlisted_product_ids(request),
    })


def search_view(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.filter(is_active=True)
    if query:
        products = products.filter(Q(name__icontains=query) | Q(description__icontains=query))
    title = f'Results for "{query}"' if query else "Search"
    facet_options = Category.objects.filter(is_active=True, id__in=products.values_list("category_id", flat=True).distinct())
    return _render_product_list(
        request, products, title=title,
        breadcrumbs=[("Home", reverse("web-home")), (title, None)],
        facet_param="category", facet_options=facet_options,
    )


def category_list_view(request):
    categories = Category.objects.filter(is_active=True).prefetch_related("subcategories")
    return render(request, "web/category_list.html", {"categories": categories})


def category_detail_view(request, slug):
    category = get_object_or_404(Category, slug=slug, is_active=True)
    products = Product.objects.filter(is_active=True, category=category)
    return _render_product_list(
        request, products, title=category.name,
        breadcrumbs=[("Home", reverse("web-home")), (category.name, None)],
        facet_param="subcategory", facet_options=category.subcategories.filter(is_active=True),
    )


def brand_detail_view(request, slug):
    brand = get_object_or_404(Brand, slug=slug, is_active=True)
    products = Product.objects.filter(is_active=True, brand=brand)
    facet_options = Category.objects.filter(is_active=True, id__in=products.values_list("category_id", flat=True).distinct())
    return _render_product_list(
        request, products, title=brand.name,
        breadcrumbs=[("Home", reverse("web-home")), (brand.name, None)],
        facet_param="category", facet_options=facet_options,
    )


def collection_detail_view(request, slug):
    collection = get_object_or_404(Collection, slug=slug, is_active=True)
    products = collection.products.filter(is_active=True)
    facet_options = Category.objects.filter(is_active=True, id__in=products.values_list("category_id", flat=True).distinct())
    return _render_product_list(
        request, products, title=collection.title,
        breadcrumbs=[("Home", reverse("web-home")), (collection.title, None)],
        facet_param="category", facet_options=facet_options,
    )


def deals_view(request):
    deal_product_ids = [deal.product_id for deal in discovery_services.get_live_flash_deals()]
    products = Product.objects.filter(id__in=deal_product_ids, is_active=True)
    facet_options = Category.objects.filter(is_active=True, id__in=products.values_list("category_id", flat=True).distinct())
    return _render_product_list(
        request, products, title="Deals",
        breadcrumbs=[("Home", reverse("web-home")), ("Deals", None)],
        facet_param="category", facet_options=facet_options,
    )


def new_arrivals_view(request):
    products = Product.objects.filter(is_active=True)
    facet_options = Category.objects.filter(is_active=True, id__in=products.values_list("category_id", flat=True).distinct())
    return _render_product_list(
        request, products, title="New Arrivals",
        breadcrumbs=[("Home", reverse("web-home")), ("New Arrivals", None)],
        facet_param="category", facet_options=facet_options, default_sort="newest",
    )


def seller_detail_view(request, slug):
    seller = get_object_or_404(Seller, slug=slug)
    products = Product.objects.filter(is_active=True, seller=seller)
    return render(request, "web/seller_detail.html", {
        "seller": seller,
        "product_count": products.count(),
        "products": products.order_by("-sold_count")[:24],
        "wishlisted_ids": wishlist_services.get_wishlisted_product_ids(request),
    })


def product_detail_view(request, slug):
    product = get_object_or_404(
        Product.objects.select_related("category", "brand", "seller"), slug=slug, is_active=True
    )
    related = Product.objects.filter(category=product.category, is_active=True).exclude(id=product.id)[:6]
    breadcrumbs = [("Home", reverse("web-home"))]
    if product.category:
        breadcrumbs.append((product.category.name, reverse("web-category-detail", args=[product.category.slug])))
    breadcrumbs.append((product.name, None))

    wishlisted_ids = wishlist_services.get_wishlisted_product_ids(request)
    return render(request, "web/product_detail.html", {
        "product": product,
        "breadcrumbs": breadcrumbs,
        "variants": product.variants.all(),
        "reviews": product.reviews.select_related("user").order_by("-created_at")[:10],
        "related_products": related,
        "wishlisted_ids": wishlisted_ids,
        "is_wishlisted": product.id in wishlisted_ids,
    })


def wishlist_view(request):
    items = wishlist_services.list_items(request)
    return render(request, "web/wishlist.html", {
        "items": items,
        "wishlisted_ids": {item.product_id for item in items},
        "active_nav": "wishlist",
        "orders_count": _orders_count(request),
    })


@require_http_methods(["POST"])
def wishlist_toggle_view(request):
    product = get_object_or_404(Product, id=request.POST.get("product_id"), is_active=True)
    wishlisted = wishlist_services.toggle(request, product)
    messages.success(request, f"{'Added' if wishlisted else 'Removed'} {product.name} {'to' if wishlisted else 'from'} your wishlist.")
    target = _safe_redirect_target(request, request.POST.get("next"), reverse("web-wishlist"))
    return redirect(target)


@require_http_methods(["POST"])
def wishlist_move_to_cart_view(request, product_id):
    product = get_object_or_404(Product, id=product_id, is_active=True)
    cart = cart_services.get_or_create_cart(request)
    cart_services.add_item(cart, product=product, variant=None, qty=1)
    wishlist_services.remove(request, product)
    messages.success(request, f"Moved {product.name} to your cart.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-wishlist")))

def cart_view(request):
    cart = cart_services.get_or_create_cart(request)
    totals = cart_services.compute_totals(cart)
    return render(request, "web/cart.html", {"totals": totals})


@require_http_methods(["POST"])
def cart_add_view(request):
    product = get_object_or_404(Product, id=request.POST.get("product_id"), is_active=True)
    variant_id = request.POST.get("variant_id")
    variant = get_object_or_404(ProductVariant, id=variant_id, product=product) if variant_id else None
    try:
        qty = max(1, int(request.POST.get("qty", 1)))
    except (TypeError, ValueError):
        qty = 1

    cart = cart_services.get_or_create_cart(request)
    cart_services.add_item(cart, product=product, variant=variant, qty=qty)

    if request.POST.get("buy_now"):
        return redirect("web-checkout")

    messages.success(request, f"Added {product.name} to your cart.")
    target = _safe_redirect_target(request, request.POST.get("next"), reverse("web-cart"))
    return redirect(target)


@require_http_methods(["POST"])
def cart_item_update_view(request, item_id):
    cart = cart_services.get_or_create_cart(request)
    item = get_object_or_404(CartItem, id=item_id, cart=cart)
    try:
        qty = int(request.POST.get("qty", 1))
    except (TypeError, ValueError):
        qty = 1

    if qty < 1:
        item.delete()
    else:
        cart_services.update_item_qty(item, qty)
    return redirect("web-cart")


@require_http_methods(["POST"])
def cart_item_remove_view(request, item_id):
    cart = cart_services.get_or_create_cart(request)
    get_object_or_404(CartItem, id=item_id, cart=cart).delete()
    return redirect("web-cart")


@require_http_methods(["POST"])
def cart_coupon_apply_view(request):
    from cart.models import Coupon

    cart = cart_services.get_or_create_cart(request)
    code = request.POST.get("code", "").strip()

    coupon = Coupon.objects.filter(code__iexact=code).first()
    if coupon is None:
        messages.error(request, "That promo code isn't valid.")
        return redirect("web-cart")

    totals = cart_services.compute_totals(cart)
    error = coupon.validate_for_subtotal(totals["subtotal"])
    if error:
        messages.error(request, error)
        return redirect("web-cart")

    cart.applied_coupon = coupon
    cart.save(update_fields=["applied_coupon"])
    messages.success(request, f"Promo code {coupon.code} applied.")
    return redirect("web-cart")


def _resolve_checkout_payment_method(request):
    token_id = request.POST.get("payment_token_id")
    if token_id:
        token = get_object_or_404(PaymentMethodToken, id=token_id, user=request.user)
        code = "card" if token.brand in CARD_BRANDS else "mobile_money"
        return get_object_or_404(PaymentMethod, code=code, is_active=True)
    return get_object_or_404(PaymentMethod, id=request.POST.get("payment_method_id"), is_active=True)


@login_required(login_url="web-login")
def checkout_view(request):
    cart = cart_services.get_or_create_cart(request)
    totals = cart_services.compute_totals(cart)
    if not totals["items"]:
        messages.info(request, "Your cart is empty.")
        return redirect("web-cart")

    addresses = request.user.addresses.all()
    delivery_methods = DeliveryMethod.objects.filter(is_active=True)
    payment_tokens = PaymentMethodToken.objects.filter(user=request.user)
    cod_method = PaymentMethod.objects.filter(code="cash_on_delivery", is_active=True).first()
    payment_methods = PaymentMethod.objects.filter(is_active=True) if not payment_tokens.exists() else None

    if request.method == "POST":
        address = get_object_or_404(Address, id=request.POST.get("address_id"), user=request.user)
        delivery_method = get_object_or_404(DeliveryMethod, id=request.POST.get("delivery_method_id"), is_active=True)
        payment_method = _resolve_checkout_payment_method(request)

        if payment_method.code in HUBTEL_PAYMENT_METHOD_CODES:
            try:
                pending = start_hubtel_checkout(
                    user=request.user, cart=cart, address=address, delivery_method=delivery_method,
                    payment_method=payment_method,
                    callback_url=request.build_absolute_uri(reverse("payment-webhook", kwargs={"gateway": "hubtel"})),
                    return_url=request.build_absolute_uri(reverse("web-hubtel-return")),
                    cancellation_url=request.build_absolute_uri(reverse("web-checkout")),
                )
            except HubtelCheckoutError as exc:
                messages.error(request, str(exc))
                return redirect("web-checkout")

            if not pending.checkout_url:
                messages.error(request, "Couldn't start the Hubtel checkout. Please try again.")
                return redirect("web-checkout")
            return redirect(pending.checkout_url)

        try:
            order = place_order(
                user=request.user, cart=cart, address=address,
                delivery_method=delivery_method, payment_method=payment_method,
            )
        except OrderPlacementError as exc:
            messages.error(request, str(exc))
            return redirect("web-checkout")

        return redirect("web-order-confirmation", order_number=order.order_number)

    default_delivery = delivery_methods.first()
    totals = cart_services.compute_totals(cart, delivery_fee=default_delivery.price if default_delivery else 0)
    return render(request, "web/checkout.html", {
        "totals": totals, "addresses": addresses, "delivery_methods": delivery_methods,
        "payment_tokens": payment_tokens, "cod_method": cod_method, "payment_methods": payment_methods,
    })


@login_required(login_url="web-login")
def hubtel_return_view(request):
    """Where the browser lands after Hubtel's hosted checkout page. The
    webhook usually finalizes the order first, but the browser can get here
    before that delivery arrives, so this re-checks directly with Hubtel."""
    reference = request.GET.get("clientReference") or request.GET.get("reference")
    if not reference:
        messages.error(request, "Missing payment reference.")
        return redirect("web-checkout")

    pending = get_object_or_404(PendingCheckout, reference=reference, user=request.user)

    if pending.status == PendingCheckout.Status.PENDING:
        try:
            result = HubtelGateway().check_status(reference)
        except PaymentGatewayError:
            result = {"status": "pending"}

        if result["status"] == "success":
            pending = finalize_pending_checkout(pending)
        elif result["status"] == "failed":
            pending = mark_pending_checkout_failed(pending, "Payment failed or was cancelled.")

    if pending.status == PendingCheckout.Status.PAID and pending.order:
        return redirect("web-order-confirmation", order_number=pending.order.order_number)
    if pending.status == PendingCheckout.Status.FAILED:
        messages.error(request, pending.failure_reason or "Payment failed or was cancelled.")
        return redirect("web-checkout")

    messages.info(request, "We're still confirming your payment - this can take a moment. Please check back shortly.")
    return redirect("web-account-orders")


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def address_add_view(request):
    serializer = AddressSerializer(data=request.POST, context={"request": request})
    if serializer.is_valid():
        serializer.save()
        messages.success(request, "Address added.")
    else:
        messages.error(request, "Please check the address fields and try again.")
    target = _safe_redirect_target(request, request.POST.get("next"), reverse("web-checkout"))
    return redirect(target)


@login_required(login_url="web-login")
def order_confirmation_view(request, order_number):
    order = get_object_or_404(
        Order.objects.prefetch_related("items__product", "status_history", "return_requests__items"),
        order_number=order_number, user=request.user,
    )
    eligibility = eligible_order_items(order)
    return render(request, "web/order_confirmation.html", {
        "order": order,
        "just_placed": order.status == Order.Status.PROCESSING and order.status_history.count() <= 1,
        "return_eligibility": eligibility,
        "can_request_return": bool(eligibility),
        "return_requests": order.return_requests.all(),
    })


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def order_request_return_view(request, order_number):
    order = get_object_or_404(Order, order_number=order_number, user=request.user)
    order_item_ids = request.POST.getlist("order_item_id")
    qtys = request.POST.getlist("qty")
    lines = [
        {"order_item_id": oid, "qty": qty}
        for oid, qty in zip(order_item_ids, qtys)
        if oid and qty
    ]

    try:
        request_return(order=order, user=request.user, lines=lines, reason=request.POST.get("reason", ""))
        messages.success(request, "Your return request has been submitted.")
    except ReturnError as exc:
        messages.error(request, exc.message)
    return redirect("web-order-confirmation", order_number=order.order_number)

@login_required(login_url="web-login")
def notifications_view(request):
    notifications = Notification.objects.filter(user=request.user)[:50]
    return render(request, "web/notifications.html", {"notifications": notifications})


def support_view(request):
    if request.method == "POST":
        if not request.user.is_authenticated:
            messages.error(request, "Please log in to contact support.")
            return redirect("web-login")

        serializer = SupportTicketCreateSerializer(data=request.POST)
        if serializer.is_valid():
            serializer.save(user=request.user)
            messages.success(request, "Thanks — we'll get back to you soon.")
            return redirect("web-support")
        messages.error(request, "Please check the form and try again.")

    topic = request.GET.get("topic")
    faqs = FAQ.objects.filter(is_active=True)
    if topic:
        faqs = faqs.filter(topic=topic)
    return render(request, "web/support.html", {"faqs": faqs, "topics": FAQ.Topic.choices, "active_topic": topic})


def static_page_view(request, slug):
    page = get_object_or_404(StaticPage, slug=slug)
    return render(request, "web/static_page.html", {"page": page})


ORDER_TAB_STATUSES = {
    "processing": [Order.Status.PROCESSING],
    "in-transit": [Order.Status.SHIPPED, Order.Status.OUT_FOR_DELIVERY],
    "delivered": [Order.Status.DELIVERED],
    "cancelled": [Order.Status.CANCELLED],
}


@customer_account_required
def account_overview_view(request):
    recent_orders = (
        Order.objects.filter(user=request.user).prefetch_related("items__product").order_by("-placed_at")[:3]
    )
    return render(request, "web/account_overview.html", {
        "active_nav": "overview",
        "orders_count": _orders_count(request),
        "wishlist_items_count": wishlist_services.count(request),
        "reviews_count": Review.objects.filter(user=request.user).count(),
        "recent_orders": recent_orders,
        "seller_application": SellerApplication.objects.filter(user=request.user).order_by("-submitted_at").first(),
    })


@customer_account_required
@require_http_methods(["POST"])
def account_preference_toggle_view(request):
    field = request.POST.get("field")
    if field not in {"push_notifications_enabled", "email_offers_enabled"}:
        messages.error(request, "Unknown preference.")
        return redirect("web-account-overview")

    setattr(request.user, field, not getattr(request.user, field))
    request.user.save(update_fields=[field])
    return redirect("web-account-overview")


@customer_account_required
def account_edit_view(request):
    if request.method == "POST":
        full_name = request.POST.get("full_name", "").strip()
        phone = request.POST.get("phone", "").strip()
        if not full_name:
            messages.error(request, "Please enter your name.")
        else:
            request.user.full_name = full_name
            request.user.phone = phone or None
            try:
                request.user.save(update_fields=["full_name", "phone"])
                messages.success(request, "Profile updated.")
                return redirect("web-account-overview")
            except IntegrityError:
                messages.error(request, "That phone number is already in use by another account.")

    return render(request, "web/account_edit.html", {"active_nav": "overview"})


@customer_account_required
def account_orders_view(request):
    tab = request.GET.get("status", "")
    orders = Order.objects.filter(user=request.user).prefetch_related("items__product").order_by("-placed_at")
    if tab in ORDER_TAB_STATUSES:
        orders = orders.filter(status__in=ORDER_TAB_STATUSES[tab])

    return render(request, "web/account_orders.html", {
        "active_nav": "orders",
        "orders_count": _orders_count(request),
        "orders": orders,
        "active_tab": tab,
    })


@customer_account_required
def account_addresses_view(request):
    return render(request, "web/account_addresses.html", {
        "active_nav": "addresses",
        "orders_count": _orders_count(request),
        "addresses": request.user.addresses.all(),
    })


@customer_account_required
def address_edit_view(request, address_id):
    address = get_object_or_404(Address, id=address_id, user=request.user)
    if request.method == "POST":
        serializer = AddressSerializer(address, data=request.POST, context={"request": request})
        if serializer.is_valid():
            serializer.save()
            messages.success(request, "Address updated.")
            return redirect("web-account-addresses")
        messages.error(request, "Please check the address fields and try again.")

    return render(request, "web/account_address_edit.html", {
        "active_nav": "addresses",
        "orders_count": _orders_count(request),
        "address": address,
    })


@customer_account_required
@require_http_methods(["POST"])
def address_delete_view(request, address_id):
    address = get_object_or_404(Address, id=address_id, user=request.user)
    try:
        handle_deletion(address)
    except CannotDeleteOnlyDefaultError as exc:
        messages.error(request, exc.message)
        return redirect("web-account-addresses")
    address.delete()
    messages.success(request, "Address removed.")
    return redirect("web-account-addresses")


@customer_account_required
@require_http_methods(["POST"])
def address_set_default_view(request, address_id):
    address = get_object_or_404(Address, id=address_id, user=request.user)
    address.is_default = True
    address.save(update_fields=["is_default"])
    messages.success(request, "Default address updated.")
    return redirect("web-account-addresses")


@customer_account_required
def account_payment_methods_view(request):
    return render(request, "web/account_payment_methods.html", {
        "active_nav": "payment-methods",
        "orders_count": _orders_count(request),
        "payment_methods": request.user.payment_methods.all(),
    })


@customer_account_required
@require_http_methods(["POST"])
def payment_method_add_view(request):
    data = {
        "gateway": request.POST.get("gateway") or PaymentMethodToken.Gateway.PAYSTACK,
        "brand": request.POST.get("brand", "").strip(),
        "last4": request.POST.get("last4", "").strip(),
        "expiry_month": request.POST.get("expiry_month") or None,
        "expiry_year": request.POST.get("expiry_year") or None,
        "token": f"tok_demo_{secrets.token_hex(6)}",
    }
    serializer = PaymentMethodTokenCreateSerializer(data=data)
    if serializer.is_valid():
        serializer.save(user=request.user)
        messages.success(request, "Payment method added.")
    else:
        messages.error(request, "Please check the payment method fields and try again.")
    return redirect("web-account-payment-methods")


@customer_account_required
@require_http_methods(["POST"])
def payment_method_delete_view(request, token_id):
    token = get_object_or_404(PaymentMethodToken, id=token_id, user=request.user)
    try:
        handle_deletion(token)
    except CannotDeleteOnlyDefaultError as exc:
        messages.error(request, exc.message)
        return redirect("web-account-payment-methods")
    token.delete()
    messages.success(request, "Payment method removed.")
    return redirect("web-account-payment-methods")


@customer_account_required
@require_http_methods(["POST"])
def payment_method_set_default_view(request, token_id):
    token = get_object_or_404(PaymentMethodToken, id=token_id, user=request.user)
    token.is_default = True
    token.save(update_fields=["is_default"])
    messages.success(request, "Default payment method updated.")
    return redirect("web-account-payment-methods")


# -- Seller dashboard -----------------------------------------------------

SELLER_ORDER_TAB_STATUSES = {
    "processing": [Order.Status.PROCESSING],
    "shipped": [Order.Status.SHIPPED, Order.Status.OUT_FOR_DELIVERY],
    "delivered": [Order.Status.DELIVERED],
    "cancelled": [Order.Status.CANCELLED],
}


def seller_required(view_func):
    @wraps(view_func)
    @login_required(login_url="web-login")
    def wrapper(request, *args, **kwargs):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            messages.error(request, "You need an approved seller account to view this page.")
            return redirect("web-home")
        return view_func(request, seller, *args, **kwargs)

    return wrapper


def superadmin_required(view_func):
    @wraps(view_func)
    @login_required(login_url="web-login")
    def wrapper(request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "You don't have access to the admin console.")
            return redirect("web-home")
        return view_func(request, *args, **kwargs)

    return wrapper


def _seller_order_qs(seller):
    return (
        Order.objects.filter(items__product__seller=seller)
        .distinct()
        .prefetch_related("items__product")
        .order_by("-placed_at")
    )


def _seller_line_total(order, seller) -> Decimal:
    return sum(
        (item.line_total for item in order.items.all() if item.product.seller_id == seller.id),
        Decimal("0.00"),
    )


@seller_required
def seller_overview_view(request, seller):
    now = timezone.now()
    window_start = now - timedelta(days=30)

    seller_items = OrderItem.objects.filter(product__seller=seller).exclude(order__status=Order.Status.CANCELLED)
    revenue_30d = seller_items.filter(order__placed_at__gte=window_start).aggregate(
        total=Sum("unit_price")
    )["total"] or Decimal("0.00")
    orders_30d = (
        Order.objects.filter(items__product__seller=seller, placed_at__gte=window_start)
        .exclude(status=Order.Status.CANCELLED)
        .distinct()
        .count()
    )
    product_views = Product.objects.filter(seller=seller).aggregate(total=Sum("view_count"))["total"] or 0

    revenue_by_day = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_total = seller_items.filter(order__placed_at__date=day).aggregate(total=Sum("unit_price"))[
            "total"
        ] or Decimal("0.00")
        revenue_by_day.append({"label": day.strftime("%a"), "amount": day_total})
    max_day_revenue = max((d["amount"] for d in revenue_by_day), default=Decimal("0.00")) or Decimal("1.00")
    for d in revenue_by_day:
        d["percent"] = round(float(d["amount"] / max_day_revenue) * 100) if max_day_revenue else 0

    top_products = Product.objects.filter(seller=seller).order_by("-sold_count")[:4]
    recent_orders = list(_seller_order_qs(seller)[:5])
    for order in recent_orders:
        order.seller_total = _seller_line_total(order, seller)

    return render(request, "web/seller_overview.html", {
        "active_nav": "overview",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "revenue_30d": revenue_30d,
        "orders_30d": orders_30d,
        "product_views": product_views,
        "revenue_by_day": revenue_by_day,
        "top_products": top_products,
        "recent_orders": recent_orders,
    })


@seller_required
def seller_products_view(request, seller):
    products = Product.objects.filter(seller=seller).select_related("category").order_by("-created_at")
    query = request.GET.get("q", "").strip()
    if query:
        products = products.filter(name__icontains=query)

    return render(request, "web/seller_products.html", {
        "active_nav": "products",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "products": products,
        "query": query,
    })


def _unique_product_slug(name: str) -> str:
    base = slugify(name) or "product"
    slug = base
    suffix = 1
    while Product.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


def _save_product_from_form(request, seller, product=None):
    name = request.POST.get("name", "").strip()
    category_id = request.POST.get("category_id")
    price = request.POST.get("price", "").strip()
    stock_qty = request.POST.get("stock_qty", "0").strip()
    description = request.POST.get("description", "").strip()
    original_price = request.POST.get("original_price", "").strip()
    image_urls = [u.strip() for u in request.POST.get("image_urls", "").splitlines() if u.strip()]

    if not name or not category_id or not price:
        messages.error(request, "Name, category, and price are required.")
        return None

    category = get_object_or_404(Category, id=category_id)

    if product is None:
        product = Product(
            seller=seller,
            slug=_unique_product_slug(name),
            sku=f"SKU-{secrets.token_hex(4).upper()}",
        )

    product.name = name
    product.category = category
    product.price = price
    product.original_price = original_price or None
    product.stock_qty = stock_qty or 0
    product.description = description
    product.is_featured = bool(request.POST.get("is_featured"))
    product.is_active = bool(request.POST.get("is_active", "1"))
    product.is_returnable = bool(request.POST.get("is_returnable"))
    try:
        product.return_window_days = max(0, int(request.POST.get("return_window_days") or 7))
    except ValueError:
        product.return_window_days = 7
    product.save()

    if image_urls:
        product.images.all().delete()
        for i, url in enumerate(image_urls):
            ProductImage.objects.create(product=product, external_url=url, display_order=i)

    return product


@seller_required
def seller_product_add_view(request, seller):
    if request.method == "POST":
        product = _save_product_from_form(request, seller)
        if product is not None:
            messages.success(request, f'"{product.name}" was added to your store.')
            return redirect("web-seller-products")

    return render(request, "web/seller_product_form.html", {
        "active_nav": "products",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "categories": Category.objects.filter(is_active=True),
        "product": None,
    })


@seller_required
def seller_product_edit_view(request, seller, product_id):
    product = get_object_or_404(Product, id=product_id, seller=seller)
    if request.method == "POST":
        saved = _save_product_from_form(request, seller, product=product)
        if saved is not None:
            messages.success(request, f'"{saved.name}" was updated.')
            return redirect("web-seller-products")

    return render(request, "web/seller_product_form.html", {
        "active_nav": "products",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "categories": Category.objects.filter(is_active=True),
        "product": product,
    })


@seller_required
@require_http_methods(["POST"])
def seller_product_toggle_view(request, seller, product_id):
    product = get_object_or_404(Product, id=product_id, seller=seller)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    return redirect("web-seller-products")


@seller_required
@require_http_methods(["POST"])
def seller_product_delete_view(request, seller, product_id):
    product = get_object_or_404(Product, id=product_id, seller=seller)
    name = product.name
    try:
        product.delete()
        messages.success(request, f'"{name}" was deleted.')
    except ProtectedError:
        product.is_active = False
        product.save(update_fields=["is_active"])
        messages.error(request, f'"{name}" has existing orders, so it was deactivated instead of deleted.')
    return redirect("web-seller-products")


@seller_required
def seller_orders_view(request, seller):
    tab = request.GET.get("status", "")
    orders = _seller_order_qs(seller)
    if tab in SELLER_ORDER_TAB_STATUSES:
        orders = orders.filter(status__in=SELLER_ORDER_TAB_STATUSES[tab])
    orders = list(orders)
    for order in orders:
        order.seller_total = _seller_line_total(order, seller)

    return render(request, "web/seller_orders.html", {
        "active_nav": "orders",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "orders": orders,
        "active_tab": tab,
    })


@seller_required
@require_http_methods(["POST"])
def seller_order_status_update_view(request, seller, order_number):
    order = get_object_or_404(Order, order_number=order_number, items__product__seller=seller)
    new_status = request.POST.get("status", "")
    courier_name = request.POST.get("courier_name", "").strip()
    tracking_number = request.POST.get("tracking_number", "").strip()

    try:
        if new_status and new_status != order.status:
            order.transition_to(new_status, note="Updated by seller.")
            messages.success(request, f"Order {order.order_number} marked as {order.get_status_display()}.")

        if order.status == Order.Status.SHIPPED and (courier_name or tracking_number):
            shipment, _ = Shipment.objects.get_or_create(order=order)
            shipment.courier_name = courier_name
            shipment.tracking_number = tracking_number
            shipment.save(update_fields=["courier_name", "tracking_number", "updated_at"])
            messages.success(request, f"Tracking info saved for order {order.order_number}.")
    except ValueError:
        messages.error(request, f"Can't move order {order.order_number} to that status.")
    return redirect("web-seller-orders")


RETURN_TAB_STATUSES = {
    "requested": [ReturnRequest.Status.REQUESTED],
    "approved": [ReturnRequest.Status.APPROVED],
    "rejected": [ReturnRequest.Status.REJECTED],
}


@seller_required
def seller_order_returns_view(request, seller):
    tab = request.GET.get("status", "requested")
    returns = ReturnRequest.objects.filter(items__order_item__product__seller=seller).distinct().prefetch_related(
        "items__order_item__product", "order"
    ).order_by("-requested_at")
    if tab in RETURN_TAB_STATUSES:
        returns = returns.filter(status__in=RETURN_TAB_STATUSES[tab])

    return render(request, "web/seller_order_returns.html", {
        "active_nav": "order-returns",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "returns": returns,
        "active_tab": tab,
    })


@seller_required
@require_http_methods(["POST"])
def seller_order_return_resolve_view(request, seller, return_id):
    return_request = get_object_or_404(
        ReturnRequest, id=return_id, items__order_item__product__seller=seller
    )
    action = request.POST.get("action")
    try:
        resolve_return_request(
            return_request=return_request, action=action, seller_note=request.POST.get("seller_note", "").strip()
        )
        messages.success(request, f"Return request {'approved' if action == 'approve' else 'rejected'}.")
    except ReturnError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-order-returns")


@seller_required
def seller_payouts_view(request, seller):
    payouts = Payout.objects.filter(seller=seller)
    next_payout = payouts.filter(status=Payout.Status.SCHEDULED).first()
    pending_request = payouts.filter(status=Payout.Status.REQUESTED).first()
    history = payouts.exclude(status=Payout.Status.REQUESTED)

    return render(request, "web/seller_payouts.html", {
        "active_nav": "payouts",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "available_balance": get_available_balance(seller),
        "next_payout": next_payout,
        "pending_request": pending_request,
        "payout_history": history,
    })


@seller_required
@require_http_methods(["POST"])
def seller_payout_request_view(request, seller):
    amount = request.POST.get("amount", "").strip()
    method = request.POST.get("method", "").strip()
    account_details = request.POST.get("account_details", "").strip()

    try:
        amount = Decimal(amount)
    except (InvalidOperation, ValueError):
        messages.error(request, "Enter a valid amount to withdraw.")
        return redirect("web-seller-payouts")

    try:
        request_withdrawal(seller, amount=amount, method=method, account_details=account_details)
        messages.success(request, f"Withdrawal request for GH₵{amount} submitted for review.")
    except PayoutError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-payouts")


@seller_required
def seller_export_report_view(request, seller):
    import csv

    from django.http import HttpResponse

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{seller.slug}-orders.csv"'
    writer = csv.writer(response)
    writer.writerow(["Order", "Customer", "Date", "Status", "Your total (GHS)"])
    for order in _seller_order_qs(seller):
        writer.writerow([
            order.order_number,
            order.delivery_recipient_name,
            order.placed_at.strftime("%Y-%m-%d"),
            order.get_status_display(),
            _seller_line_total(order, seller),
        ])
    return response


@seller_required
def seller_settings_view(request, seller):
    if request.method == "POST":
        seller.business_name = request.POST.get("business_name", seller.business_name).strip()
        category_id = request.POST.get("primary_category_id")
        if category_id:
            seller.primary_category = get_object_or_404(Category, id=category_id)
        seller.support_phone = request.POST.get("support_phone", "").strip()
        seller.tagline = request.POST.get("tagline", "").strip()
        seller.save(update_fields=["business_name", "primary_category", "support_phone", "tagline"])
        messages.success(request, "Store settings saved.")
        return redirect("web-seller-settings")

    return render(request, "web/seller_settings.html", {
        "active_nav": "settings",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "categories": Category.objects.filter(is_active=True),
    })
