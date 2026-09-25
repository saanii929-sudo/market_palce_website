import csv
import secrets
import datetime
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
from django.db import IntegrityError, transaction
from django.db.models import Max, ProtectedError, Q, Sum
from django.http import HttpResponse
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
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from accounts.models import Address, OTPCode, User
from accounts.serializers import AddressSerializer, RegisterSerializer
from accounts.services import social as social_service
from accounts.services.otp import OTPVerificationError, send_otp, verify_otp
from cart import services as cart_services
from cart.models import CartItem
from cart.services import CartError
from catalog.models import Brand, Category, Collection, FlashDeal, Product, ProductImage, ProductVariant, Seller
from core.defaults import CannotDeleteOnlyDefaultError, handle_deletion
from core.sms import get_sms_backend
from discovery import services as discovery_services
from notifications.models import Notification
from orders.models import (
    DeliveryMethod,
    Order,
    OrderItem,
    PaymentMethod,
    PendingCheckout,
    RefundRequest,
    ReturnRequest,
    SellerOrder,
    Shipment,
)
from orders.services.hubtel_checkout import (
    HubtelCheckoutError,
    finalize_pending_checkout,
    mark_pending_checkout_failed,
    start_hubtel_checkout,
)
from orders.services.order_placement import OrderPlacementError, place_order
from orders.services.payment_gateway import HUBTEL_PAYMENT_METHOD_CODES, HubtelGateway, PaymentGatewayError
from orders.services.refunds import RefundError, advance_refund_request, is_refund_eligible, request_refund
from orders.services.returns import ReturnError, eligible_order_items, request_return, resolve_return_request
from payments.models import PaymentMethodToken
from payments.serializers import PaymentMethodTokenCreateSerializer
from reviews.models import Review
from reviews.serializers import ReviewCreateSerializer, ReviewFlagCreateSerializer
from reviews.services import ReviewModerationError, flag_review
from deliveries.services import build_tracking_payload, get_delivery_for, list_nearby_riders_for_seller_order
from riders.models import RiderProfile
from sellers.models import (
    BulkUploadJob,
    Payout,
    PayoutAccount,
    SellerApplication,
    SellerSubscription,
    SubscriptionPlan,
)
from sellers.tasks import process_bulk_upload
from sellers.services import (
    PayoutError,
    RiderRequestError,
    SellerApplicationError,
    SubscriptionError,
    add_favorite_rider,
    block_rider,
    finalize_subscription_payment,
    get_active_subscription,
    get_available_balance,
    mark_subscription_failed,
    remove_favorite_rider,
    request_rider_for_seller_order,
    request_withdrawal,
    start_subscription_checkout,
    submit_kyc,
    unblock_rider,
)
from support.models import FAQ, SupportContact
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

    google_ctx = {
        "google_client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "google_login_uri": request.build_absolute_uri(reverse("web-google-callback")),
    }

    if request.method == "POST":
        channel = request.POST.get("channel", "email")
        raw_identifier = request.POST.get("identifier") if channel == "email" else request.POST.get("identifier_phone")
        identifier = (raw_identifier or "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=identifier, password=password) if identifier and password else None
        if user is None:
            return render(request, "web/login.html", {
                "errors": "Invalid credentials.", "channel": channel, "identifier": identifier, **google_ctx,
            })
        if not user.is_verified:
            return render(request, "web/login.html", {
                "errors": "Please verify your account before logging in.", "channel": channel, "identifier": identifier,
                **google_ctx,
            })

        if request.session.session_key:
            cart_services.merge_guest_cart_into_user_cart(request.session.session_key, user)
            wishlist_services.merge_guest_wishlist_into_user_wishlist(request.session.session_key, user)
            discovery_services.merge_guest_recently_viewed_into_user(request.session.session_key, user)

        django_login(request, user)
        messages.success(request, f"Welcome back, {user.full_name or user.email or user.phone}!")
        return redirect(_post_login_redirect_target(user))

    return render(request, "web/login.html", {"channel": "email", **google_ctx})


def logout_view(request):
    django_logout(request)
    return redirect("web-home")


@csrf_exempt
@require_http_methods(["POST"])
def google_login_callback_view(request):
    if request.user.is_authenticated:
        return redirect(_post_login_redirect_target(request.user))

    csrf_cookie = request.COOKIES.get("g_csrf_token")
    csrf_body = request.POST.get("g_csrf_token")
    if not csrf_cookie or not csrf_body or csrf_cookie != csrf_body:
        messages.error(request, "Google sign-in failed a security check. Please try again.")
        return redirect("web-login")

    try:
        profile = social_service.verify_google_token(request.POST.get("credential", ""))
    except social_service.SocialAuthError as exc:
        messages.error(request, exc.message)
        return redirect("web-login")

    email = profile.get("email")
    if not email:
        messages.error(request, "Google didn't share an email address for this account.")
        return redirect("web-login")

    user = User.objects.filter(email__iexact=email).first()
    if user is None:
        user = User(email=email, full_name=profile.get("full_name", ""), is_email_verified=True)
        user.set_unusable_password()
        user.save()
    elif not user.is_email_verified:
        user.is_email_verified = True
        user.save(update_fields=["is_email_verified"])

    if request.session.session_key:
        cart_services.merge_guest_cart_into_user_cart(request.session.session_key, user)
        wishlist_services.merge_guest_wishlist_into_user_wishlist(request.session.session_key, user)
        discovery_services.merge_guest_recently_viewed_into_user(request.session.session_key, user)

    user.backend = WEB_AUTH_BACKEND
    django_login(request, user)
    messages.success(request, f"Welcome, {user.full_name or user.email}!")
    return redirect(_post_login_redirect_target(user))


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
    variants = list(product.variants.all())
    default_variant = next((v for v in variants if v.in_stock), variants[0] if variants else None)

    reviewable_order_items = []
    if request.user.is_authenticated:
        reviewable_order_items = list(
            OrderItem.objects.filter(
                seller_order__order__user=request.user,
                seller_order__status=SellerOrder.Status.DELIVERED,
                product=product,
                review__isnull=True,
            ).order_by("-created_at")
        )

    return render(request, "web/product_detail.html", {
        "product": product,
        "breadcrumbs": breadcrumbs,
        "variants": variants,
        "default_variant_id": default_variant.id if default_variant else None,
        "reviews": product.reviews.exclude(status=Review.Status.REMOVED).select_related("user").order_by("-created_at")[:10],
        "reviewable_order_items": reviewable_order_items,
        "related_products": related,
        "wishlisted_ids": wishlisted_ids,
        "is_wishlisted": product.id in wishlisted_ids,
    })


@login_required
@require_http_methods(["POST"])
def product_review_add_view(request, slug):
    product = get_object_or_404(Product, slug=slug, is_active=True)
    serializer = ReviewCreateSerializer(data=request.POST, context={"request": request})
    if serializer.is_valid():
        serializer.save()
        messages.success(request, "Thanks! Your review has been posted.")
    else:
        first_error = next(iter(serializer.errors.values()))[0]
        messages.error(request, first_error)
    return redirect(reverse("web-product-detail", args=[product.slug]) + "#reviews")


@login_required
@require_http_methods(["POST"])
def review_flag_view(request, review_id):
    review = get_object_or_404(Review, id=review_id)
    serializer = ReviewFlagCreateSerializer(data=request.POST)
    if serializer.is_valid():
        try:
            flag_review(review=review, flagged_by=request.user, reason=serializer.validated_data["reason"])
            messages.success(request, "Thanks - we'll take a look.")
        except ReviewModerationError as exc:
            messages.error(request, exc.message)
    else:
        messages.error(request, "Please select a valid reason.")
    return redirect(reverse("web-product-detail", args=[review.product.slug]) + "#reviews")


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
    try:
        cart_services.add_item(cart, product=product, variant=variant, qty=qty)
    except CartError as exc:
        messages.error(request, exc.message)
        target = _safe_redirect_target(request, request.POST.get("next"), reverse("web-product-detail", args=[product.slug]))
        return redirect(target)

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
        try:
            cart_services.update_item_qty(item, qty)
        except CartError as exc:
            messages.error(request, exc.message)
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
        Order.objects.prefetch_related(
            "seller_orders__seller", "seller_orders__items__product", "seller_orders__status_history",
            "seller_orders__return_requests__items__order_item__product",
        ),
        order_number=order_number, user=request.user,
    )
    seller_orders = list(order.seller_orders.all())
    for seller_order in seller_orders:
        seller_order.return_eligibility = eligible_order_items(seller_order)
        seller_order.can_request_return = bool(seller_order.return_eligibility)
        for item in seller_order.items.all():
            item.refund_eligible, item.refund_ineligibility_reason = is_refund_eligible(item)
            item.active_refund_request = (
                RefundRequest.objects.filter(order_item=item)
                .exclude(status=RefundRequest.Status.REJECTED)
                .order_by("-requested_at")
                .first()
            )

    return render(request, "web/order_confirmation.html", {
        "order": order,
        "seller_orders": seller_orders,
        "just_placed": order.status == Order.Status.PROCESSING and all(
            so.status_history.count() <= 1 for so in seller_orders
        ),
    })


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def order_request_return_view(request, order_number, seller_order_id):
    seller_order = get_object_or_404(
        SellerOrder, id=seller_order_id, order__order_number=order_number, order__user=request.user
    )
    order_item_ids = request.POST.getlist("order_item_id")
    qtys = request.POST.getlist("qty")
    lines = [
        {"order_item_id": oid, "qty": qty}
        for oid, qty in zip(order_item_ids, qtys)
        if oid and qty
    ]

    try:
        request_return(seller_order=seller_order, user=request.user, lines=lines, reason=request.POST.get("reason", ""))
        messages.success(request, "Your return request has been submitted.")
    except ReturnError as exc:
        messages.error(request, exc.message)
    return redirect("web-order-confirmation", order_number=order_number)


@login_required(login_url="web-login")
@require_http_methods(["POST"])
def order_item_refund_request_view(request, order_number, item_id):
    order_item = get_object_or_404(
        OrderItem, id=item_id, seller_order__order__order_number=order_number, seller_order__order__user=request.user
    )
    try:
        request_refund(
            order_item=order_item,
            user=request.user,
            reason=request.POST.get("reason", ""),
            reason_detail=request.POST.get("reason_detail", "").strip(),
            refund_type=request.POST.get("refund_type") or RefundRequest.RefundType.REFUND,
        )
        messages.success(request, "Your refund request has been submitted.")
    except RefundError as exc:
        messages.error(request, exc.message)
    return redirect("web-order-confirmation", order_number=order_number)


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
    return render(request, "web/support.html", {
        "faqs": faqs, "topics": FAQ.Topic.choices, "active_topic": topic,
        "support_contacts": SupportContact.objects.filter(is_active=True),
    })


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
        Order.objects.filter(user=request.user)
        .prefetch_related("seller_orders__items__product")
        .order_by("-placed_at")[:3]
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
    orders = Order.objects.filter(user=request.user).prefetch_related(
        "seller_orders__items__product"
    ).order_by("-placed_at")
    if tab in ORDER_TAB_STATUSES:
        orders = [o for o in orders if o.status in ORDER_TAB_STATUSES[tab]]

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


def subscription_required(view_func):
    @wraps(view_func)
    @login_required(login_url="web-login")
    def wrapper(request, *args, **kwargs):
        seller = getattr(request.user, "seller_profile", None)
        if seller is None:
            messages.error(request, "You need an approved seller account to view this page.")
            return redirect("web-home")
        if get_active_subscription(seller) is None:
            messages.warning(request, "Subscribe to a SportShop Pro plan to unlock the point-of-sale suite.")
            return redirect("web-seller-subscription")
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
        SellerOrder.objects.filter(seller=seller)
        .select_related("order", "delivery_method")
        .prefetch_related("items__product")
        .order_by("-created_at")
    )


@seller_required
def seller_overview_view(request, seller):
    now = timezone.now()
    window_start = now - timedelta(days=30)

    seller_orders_live = SellerOrder.objects.filter(seller=seller).exclude(status=SellerOrder.Status.CANCELLED)
    revenue_30d = seller_orders_live.filter(order__placed_at__gte=window_start).aggregate(
        total=Sum("subtotal")
    )["total"] or Decimal("0.00")
    orders_30d = seller_orders_live.filter(order__placed_at__gte=window_start).count()
    product_views = Product.objects.filter(seller=seller).aggregate(total=Sum("view_count"))["total"] or 0

    revenue_by_day = []
    for i in range(6, -1, -1):
        day = (now - timedelta(days=i)).date()
        day_total = seller_orders_live.filter(order__placed_at__date=day).aggregate(total=Sum("subtotal"))[
            "total"
        ] or Decimal("0.00")
        revenue_by_day.append({"label": day.strftime("%a"), "amount": day_total})
    max_day_revenue = max((d["amount"] for d in revenue_by_day), default=Decimal("0.00")) or Decimal("1.00")
    for d in revenue_by_day:
        d["percent"] = round(float(d["amount"] / max_day_revenue) * 100) if max_day_revenue else 0

    top_products = Product.objects.filter(seller=seller).order_by("-sold_count")[:4]
    recent_orders = list(_seller_order_qs(seller)[:5])

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


@seller_required
def seller_bulk_upload_view(request, seller):
    if request.method == "POST":
        file = request.FILES.get("file")
        if file is None:
            messages.error(request, "Please attach a CSV file.")
        elif not file.name.lower().endswith(".csv"):
            messages.error(request, "Only CSV files are supported.")
        else:
            with transaction.atomic():
                job = BulkUploadJob.objects.create(seller=seller, file=file)
                transaction.on_commit(lambda: process_bulk_upload.delay(job.id))
            messages.success(request, "Your file is being processed - this page will show progress below.")
            return redirect("web-seller-bulk-upload")

    return render(request, "web/seller_bulk_upload.html", {
        "active_nav": "products",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "jobs": BulkUploadJob.objects.filter(seller=seller).order_by("-created_at")[:20],
    })


@seller_required
def seller_bulk_upload_errors_view(request, seller, job_id):
    job = get_object_or_404(BulkUploadJob, id=job_id, seller=seller)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="bulk-upload-{job.id}-errors.csv"'
    writer = csv.writer(response)
    writer.writerow(["row", "errors"])
    for entry in job.error_report:
        writer.writerow([entry["row"], "; ".join(entry["errors"])])
    return response


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
    images = request.FILES.getlist("images")

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

    if images:
        # Uploads add to the existing gallery rather than replacing it -
        # remove unwanted photos individually from the edit page instead.
        next_order = (product.images.aggregate(m=Max("display_order"))["m"] or -1) + 1
        for i, image in enumerate(images):
            ProductImage.objects.create(product=product, image=image, display_order=next_order + i)

    return product


@seller_required
def seller_product_add_view(request, seller):
    if request.method == "POST":
        product = _save_product_from_form(request, seller)
        if product is not None:
            messages.success(request, f'"{product.name}" was added. You can now add size/color variants below if this product needs them.')
            return redirect("web-seller-product-edit", product_id=product.id)

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
            return redirect("web-seller-product-edit", product_id=product.id)

    return render(request, "web/seller_product_form.html", {
        "active_nav": "products",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "categories": Category.objects.filter(is_active=True),
        "product": product,
        "variants": product.variants.order_by("size", "color"),
        "product_images": product.images.all(),
    })


@seller_required
@require_http_methods(["POST"])
def seller_product_image_delete_view(request, seller, product_id, image_id):
    image = get_object_or_404(ProductImage, id=image_id, product_id=product_id, product__seller=seller)
    image.delete()
    messages.success(request, "Image removed.")
    return redirect("web-seller-product-edit", product_id=product_id)


@seller_required
@require_http_methods(["POST"])
def seller_product_variant_add_view(request, seller, product_id):
    product = get_object_or_404(Product, id=product_id, seller=seller)
    size = request.POST.get("size", "").strip()
    color = request.POST.get("color", "").strip()
    sku = request.POST.get("sku", "").strip() or None
    stock_qty = request.POST.get("stock_qty", "0").strip()

    if not size and not color:
        messages.error(request, "Enter a size and/or color for the variant.")
        return redirect("web-seller-product-edit", product_id=product.id)

    try:
        stock_qty = max(0, int(stock_qty or 0))
    except ValueError:
        stock_qty = 0

    if ProductVariant.objects.filter(product=product, size=size, color=color).exists():
        messages.error(request, "A variant with that size/color combination already exists.")
        return redirect("web-seller-product-edit", product_id=product.id)
    if sku and ProductVariant.objects.filter(sku=sku).exists():
        messages.error(request, f'SKU "{sku}" is already used by another variant.')
        return redirect("web-seller-product-edit", product_id=product.id)

    ProductVariant.objects.create(product=product, size=size, color=color, sku=sku, stock_qty=stock_qty)
    messages.success(request, "Variant added.")
    return redirect("web-seller-product-edit", product_id=product.id)


@seller_required
@require_http_methods(["POST"])
def seller_product_variant_delete_view(request, seller, product_id, variant_id):
    variant = get_object_or_404(ProductVariant, id=variant_id, product_id=product_id, product__seller=seller)
    variant.delete()
    messages.success(request, "Variant removed.")
    return redirect("web-seller-product-edit", product_id=product_id)


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
def seller_order_status_update_view(request, seller, suborder_number):
    seller_order = get_object_or_404(SellerOrder, suborder_number=suborder_number, seller=seller)
    new_status = request.POST.get("status", "")
    courier_name = request.POST.get("courier_name", "").strip()
    tracking_number = request.POST.get("tracking_number", "").strip()

    try:
        if new_status and new_status != seller_order.status:
            seller_order.transition_to(new_status, note="Updated by seller.")
            messages.success(request, f"Order {suborder_number} marked as {seller_order.get_status_display()}.")

        if seller_order.status == SellerOrder.Status.SHIPPED and (courier_name or tracking_number):
            shipment, _ = Shipment.objects.get_or_create(seller_order=seller_order)
            shipment.courier_name = courier_name
            shipment.tracking_number = tracking_number
            shipment.save(update_fields=["courier_name", "tracking_number", "updated_at"])
            messages.success(request, f"Tracking info saved for order {suborder_number}.")
    except ValueError:
        messages.error(request, f"Can't move order {suborder_number} to that status.")
    return redirect("web-seller-orders")


@seller_required
def seller_order_rider_view(request, seller, suborder_number):
    seller_order = get_object_or_404(SellerOrder, suborder_number=suborder_number, seller=seller)
    delivery = get_delivery_for(seller_order)
    favorite_ids = set(seller.favorite_riders.values_list("rider_id", flat=True))
    blocked_ids = set(seller.blocked_riders.values_list("rider_id", flat=True))

    nearby_riders = list_nearby_riders_for_seller_order(seller_order)
    for rider_row in nearby_riders:
        rider_row["is_favorite"] = rider_row["rider_id"] in favorite_ids

    return render(request, "web/seller_order_rider.html", {
        "active_nav": "orders",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "seller_order": seller_order,
        "delivery": delivery,
        "tracking": build_tracking_payload(delivery) if delivery else None,
        "nearby_riders": nearby_riders,
        "favorite_riders": seller.favorite_riders.select_related("rider__user").order_by("-created_at"),
        "blocked_ids": blocked_ids,
    })


@seller_required
@require_http_methods(["POST"])
def seller_order_rider_request_view(request, seller, suborder_number):
    seller_order = get_object_or_404(SellerOrder, suborder_number=suborder_number, seller=seller)
    mode = request.POST.get("mode", "auto")
    rider = None
    if mode == "direct":
        rider = get_object_or_404(RiderProfile, id=request.POST.get("rider_id"))

    try:
        delivery, offer = request_rider_for_seller_order(seller_order, seller, mode=mode, rider=rider)
        if offer is not None:
            messages.success(request, f"Rider request sent for order {suborder_number}.")
        else:
            messages.warning(request, "No riders are available right now - we'll keep trying automatically.")
    except RiderRequestError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-order-rider", suborder_number=suborder_number)


@seller_required
def seller_riders_view(request, seller):
    return render(request, "web/seller_riders.html", {
        "active_nav": "riders",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "favorites": seller.favorite_riders.select_related("rider__user").order_by("-created_at"),
        "blocks": seller.blocked_riders.select_related("rider__user").order_by("-created_at"),
    })


@seller_required
@require_http_methods(["POST"])
def seller_rider_favorite_view(request, seller, rider_id):
    rider = get_object_or_404(RiderProfile, id=rider_id)
    add_favorite_rider(seller, rider, notes=request.POST.get("notes", "").strip())
    messages.success(request, "Added to favourites.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-seller-riders")))


@seller_required
@require_http_methods(["POST"])
def seller_rider_unfavorite_view(request, seller, rider_id):
    remove_favorite_rider(seller, rider_id)
    messages.success(request, "Removed from favourites.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-seller-riders")))


@seller_required
@require_http_methods(["POST"])
def seller_rider_block_view(request, seller, rider_id):
    rider = get_object_or_404(RiderProfile, id=rider_id)
    block_rider(seller, rider, reason=request.POST.get("reason", "").strip())
    messages.success(request, "Rider blocked - they won't be matched to your orders again.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-seller-riders")))


@seller_required
@require_http_methods(["POST"])
def seller_rider_unblock_view(request, seller, rider_id):
    unblock_rider(seller, rider_id)
    messages.success(request, "Rider unblocked.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-seller-riders")))


RETURN_TAB_STATUSES = {
    "requested": [ReturnRequest.Status.REQUESTED],
    "approved": [ReturnRequest.Status.APPROVED],
    "rejected": [ReturnRequest.Status.REJECTED],
}


@seller_required
def seller_order_returns_view(request, seller):
    tab = request.GET.get("status", "requested")
    returns = ReturnRequest.objects.filter(seller_order__seller=seller).distinct().prefetch_related(
        "items__order_item__product", "seller_order__order"
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
        ReturnRequest, id=return_id, seller_order__seller=seller
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


REFUND_TAB_STATUSES = {
    "requested": [RefundRequest.Status.REQUESTED],
    "under_review": [RefundRequest.Status.UNDER_REVIEW],
    "approved": [RefundRequest.Status.APPROVED],
    "rejected": [RefundRequest.Status.REJECTED],
    "refunded": [RefundRequest.Status.REFUNDED],
}


@seller_required
def seller_refund_requests_view(request, seller):
    tab = request.GET.get("status", "requested")
    refund_requests = RefundRequest.objects.filter(order_item__seller_order__seller=seller).select_related(
        "order_item__product", "order_item__seller_order__order", "requested_by"
    ).order_by("-requested_at")
    if tab in REFUND_TAB_STATUSES:
        refund_requests = refund_requests.filter(status__in=REFUND_TAB_STATUSES[tab])

    return render(request, "web/seller_refund_requests.html", {
        "active_nav": "refund-requests",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "refund_requests": refund_requests,
        "active_tab": tab,
    })


@seller_required
@require_http_methods(["POST"])
def seller_refund_request_action_view(request, seller, refund_request_id):
    refund_request = get_object_or_404(
        RefundRequest, id=refund_request_id, order_item__seller_order__seller=seller
    )
    action = request.POST.get("action")
    action_to_status = {
        "start_review": RefundRequest.Status.UNDER_REVIEW,
        "approve": RefundRequest.Status.APPROVED,
        "reject": RefundRequest.Status.REJECTED,
        "refund": RefundRequest.Status.REFUNDED,
    }
    new_status = action_to_status.get(action)
    if new_status is None:
        messages.error(request, "Unknown action.")
        return redirect("web-seller-refund-requests")

    refund_amount = None
    if action == "approve" and request.POST.get("refund_amount"):
        try:
            refund_amount = Decimal(request.POST["refund_amount"])
        except InvalidOperation:
            messages.error(request, "Enter a valid refund amount.")
            return redirect("web-seller-refund-requests")

    try:
        advance_refund_request(
            refund_request=refund_request, new_status=new_status, actor=request.user,
            note=request.POST.get("note", "").strip(), refund_amount=refund_amount,
        )
        messages.success(request, "Refund request updated.")
    except RefundError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-seller-refund-requests")))


def _parse_datetime_local(value: str):
    if not value:
        return None
    parsed = datetime.datetime.fromisoformat(value)
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _save_flash_deal_from_form(request, seller, flash_deal=None):
    product_id = request.POST.get("product_id")
    deal_price = request.POST.get("deal_price", "").strip()
    stock_qty = request.POST.get("stock_qty", "0").strip()

    product = Product.objects.filter(id=product_id, seller=seller).first()
    if product is None:
        messages.error(request, "Select one of your own products.")
        return None

    try:
        deal_price = Decimal(deal_price)
    except InvalidOperation:
        messages.error(request, "Enter a valid deal price.")
        return None
    if deal_price <= 0 or deal_price >= product.price:
        messages.error(request, "The deal price must be positive and less than the regular price.")
        return None

    try:
        stock_qty = max(0, int(stock_qty or 0))
    except ValueError:
        stock_qty = 0

    try:
        starts_at = _parse_datetime_local(request.POST.get("starts_at", "").strip())
        ends_at = _parse_datetime_local(request.POST.get("ends_at", "").strip())
    except ValueError:
        messages.error(request, "Enter valid start/end dates.")
        return None
    if not starts_at or not ends_at:
        messages.error(request, "Start and end dates are required.")
        return None
    if ends_at <= starts_at:
        messages.error(request, "End date must be after the start date.")
        return None

    if flash_deal is None:
        flash_deal = FlashDeal(product=product)
    else:
        flash_deal.product = product

    flash_deal.deal_price = deal_price
    flash_deal.stock_qty = stock_qty
    flash_deal.starts_at = starts_at
    flash_deal.ends_at = ends_at
    flash_deal.is_active = bool(request.POST.get("is_active", "1"))
    flash_deal.save()
    return flash_deal


@seller_required
def seller_flash_deals_view(request, seller):
    flash_deals = FlashDeal.objects.filter(product__seller=seller).select_related("product").order_by("-starts_at")
    return render(request, "web/seller_flash_deals.html", {
        "active_nav": "flash-deals",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "flash_deals": flash_deals,
    })


@seller_required
def seller_flash_deal_add_view(request, seller):
    if request.method == "POST":
        flash_deal = _save_flash_deal_from_form(request, seller)
        if flash_deal is not None:
            messages.success(request, f"Flash deal for {flash_deal.product.name} was added.")
            return redirect("web-seller-flash-deals")

    return render(request, "web/seller_flash_deal_form.html", {
        "active_nav": "flash-deals",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "products": Product.objects.filter(seller=seller, is_active=True),
        "flash_deal": None,
    })


@seller_required
def seller_flash_deal_edit_view(request, seller, flash_deal_id):
    flash_deal = get_object_or_404(FlashDeal, id=flash_deal_id, product__seller=seller)
    if request.method == "POST":
        saved = _save_flash_deal_from_form(request, seller, flash_deal=flash_deal)
        if saved is not None:
            messages.success(request, "Flash deal updated.")
            return redirect("web-seller-flash-deals")

    return render(request, "web/seller_flash_deal_form.html", {
        "active_nav": "flash-deals",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "products": Product.objects.filter(seller=seller, is_active=True),
        "flash_deal": flash_deal,
    })


@seller_required
@require_http_methods(["POST"])
def seller_flash_deal_toggle_view(request, seller, flash_deal_id):
    flash_deal = get_object_or_404(FlashDeal, id=flash_deal_id, product__seller=seller)
    flash_deal.is_active = not flash_deal.is_active
    flash_deal.save(update_fields=["is_active"])
    return redirect("web-seller-flash-deals")


@seller_required
@require_http_methods(["POST"])
def seller_flash_deal_delete_view(request, seller, flash_deal_id):
    flash_deal = get_object_or_404(FlashDeal, id=flash_deal_id, product__seller=seller)
    flash_deal.delete()
    messages.success(request, "Flash deal removed.")
    return redirect("web-seller-flash-deals")


@seller_required
def seller_payouts_view(request, seller):
    payouts = Payout.objects.filter(seller=seller)
    next_payout = payouts.filter(status=Payout.Status.SCHEDULED).first()
    pending_request = payouts.filter(status=Payout.Status.REQUESTED).first()
    history = payouts.exclude(status=Payout.Status.REQUESTED)

    kyc_application = SellerApplication.objects.filter(
        user=request.user, status=SellerApplication.Status.APPROVED
    ).order_by("-submitted_at").first()
    payout_account = PayoutAccount.objects.filter(seller=seller, is_active=True).first()

    return render(request, "web/seller_payouts.html", {
        "active_nav": "payouts",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "available_balance": get_available_balance(seller),
        "next_payout": next_payout,
        "pending_request": pending_request,
        "payout_history": history,
        "kyc_application": kyc_application,
        "payout_account": payout_account,
    })


@seller_required
@require_http_methods(["POST"])
def seller_kyc_submit_view(request, seller):
    application = SellerApplication.objects.filter(
        user=request.user, status=SellerApplication.Status.APPROVED
    ).order_by("-submitted_at").first()
    if application is None:
        messages.error(request, "No approved seller application found.")
        return redirect("web-seller-payouts")

    try:
        submit_kyc(
            application,
            bank_account_name=request.POST.get("bank_account_name", ""),
            bank_account_number=request.POST.get("bank_account_number", ""),
            bank_name=request.POST.get("bank_name", ""),
            momo_number=request.POST.get("momo_number", ""),
            momo_network=request.POST.get("momo_network", ""),
        )
        messages.success(request, "Your payout details were submitted for verification.")
    except SellerApplicationError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-payouts")


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
def seller_subscription_view(request, seller):
    plans = SubscriptionPlan.objects.filter(is_active=True)
    current = get_active_subscription(seller)
    history = seller.subscriptions.exclude(status=SellerSubscription.Status.PENDING)[:10]

    return render(request, "web/seller_subscription.html", {
        "active_nav": "subscription",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "plans": plans,
        "current_subscription": current,
        "subscription_history": history,
    })


@seller_required
@require_http_methods(["POST"])
def seller_subscription_checkout_view(request, seller):
    plan = get_object_or_404(SubscriptionPlan, id=request.POST.get("plan_id"), is_active=True)

    try:
        subscription = start_subscription_checkout(
            seller, plan,
            callback_url=request.build_absolute_uri(reverse("seller-subscription-webhook")),
            return_url=request.build_absolute_uri(reverse("web-seller-subscription-return")),
            cancellation_url=request.build_absolute_uri(reverse("web-seller-subscription")),
        )
    except SubscriptionError as exc:
        messages.error(request, exc.message)
        return redirect("web-seller-subscription")

    if not subscription.checkout_url:
        messages.error(request, "Couldn't start the subscription checkout. Please try again.")
        return redirect("web-seller-subscription")
    return redirect(subscription.checkout_url)


@seller_required
def seller_subscription_return_view(request, seller):
    """Where the browser lands after Hubtel's hosted checkout page for a
    subscription purchase. Mirrors web.views.hubtel_return_view."""
    reference = request.GET.get("clientReference") or request.GET.get("reference")
    if not reference:
        messages.error(request, "Missing payment reference.")
        return redirect("web-seller-subscription")

    subscription = get_object_or_404(SellerSubscription, reference=reference, seller=seller)

    if subscription.status == SellerSubscription.Status.PENDING:
        try:
            result = HubtelGateway().check_status(reference)
        except PaymentGatewayError:
            result = {"status": "pending"}

        if result["status"] == "success":
            subscription = finalize_subscription_payment(subscription)
        elif result["status"] == "failed":
            subscription = mark_subscription_failed(subscription, "Payment failed or was cancelled.")

    if subscription.status == SellerSubscription.Status.ACTIVE:
        messages.success(request, f"You're subscribed to {subscription.plan.name}. The POS suite is now unlocked.")
    elif subscription.status == SellerSubscription.Status.FAILED:
        messages.error(request, subscription.failure_reason or "Payment failed or was cancelled.")
    else:
        messages.info(request, "We're still confirming your payment - check back shortly.")
    return redirect("web-seller-subscription")


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
        update_fields = ["business_name", "primary_category", "support_phone", "tagline"]

        pickup_lat = request.POST.get("pickup_lat", "").strip()
        pickup_lng = request.POST.get("pickup_lng", "").strip()
        try:
            seller.pickup_lat = Decimal(pickup_lat) if pickup_lat else None
            seller.pickup_lng = Decimal(pickup_lng) if pickup_lng else None
            update_fields += ["pickup_lat", "pickup_lng"]
        except InvalidOperation:
            messages.error(request, "That pickup location doesn't look valid - please set it again.")

        if request.FILES.get("logo"):
            seller.logo = request.FILES["logo"]
            update_fields.append("logo")
        seller.save(update_fields=update_fields)
        messages.success(request, "Store settings saved.")
        return redirect("web-seller-settings")

    return render(request, "web/seller_settings.html", {
        "active_nav": "settings",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "categories": Category.objects.filter(is_active=True),
    })
