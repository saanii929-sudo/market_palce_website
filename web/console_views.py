import csv
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, ProtectedError, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_http_methods

from accounts.models import User
from catalog.models import Banner, Brand, Category, Collection, Product, Seller, Subcategory
from orders.models import DeliveryMethod, Order
from pos.models import POSSale
from sellers.models import Payout, SellerApplication, SellerSubscription
from sellers.services import (
    PayoutError,
    SellerApplicationError,
    approve_application,
    mark_payout_paid,
    reject_application,
    reject_payout,
    schedule_payout,
)

from .views import _safe_redirect_target, superadmin_required


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _base_ctx(active_nav):
    return {
        "active_nav": active_nav,
        "pending_applications_count": SellerApplication.objects.filter(
            status=SellerApplication.Status.PENDING
        ).count(),
        "pending_payouts_count": Payout.objects.filter(status=Payout.Status.REQUESTED).count(),
    }


SUBSCRIPTION_TABS = {"active", "pending", "expired", "failed"}


@superadmin_required
def console_subscriptions_view(request):
    tab = request.GET.get("status", "active")
    subscriptions = SellerSubscription.objects.select_related("seller", "plan").order_by("-created_at")
    if tab in SUBSCRIPTION_TABS:
        subscriptions = subscriptions.filter(status=tab)

    revenue = SellerSubscription.objects.filter(
        status__in=[SellerSubscription.Status.ACTIVE, SellerSubscription.Status.EXPIRED]
    ).aggregate(t=Sum("amount"))["t"] or Decimal("0.00")

    ctx = _base_ctx("subscriptions")
    ctx.update({
        "subscriptions": subscriptions,
        "active_tab": tab,
        "active_subscribers_count": SellerSubscription.objects.filter(
            status=SellerSubscription.Status.ACTIVE
        ).count(),
        "subscription_revenue": revenue,
    })
    return render(request, "web/console_subscriptions.html", ctx)


@superadmin_required
def console_subscriptions_export_view(request):
    rows = [
        [s.seller.business_name, s.plan.name, s.amount, s.status, s.created_at.date(), s.expires_at]
        for s in SellerSubscription.objects.select_related("seller", "plan").order_by("-created_at")
    ]
    return _csv_response(
        "subscriptions.csv", ["Seller", "Plan", "Amount", "Status", "Purchased", "Expires"], rows
    )


def _percent_delta(current, previous) -> float:
    if not previous:
        return 100.0 if current else 0.0
    return round((current - previous) / previous * 100, 1)


# -- Overview -----------------------------------------------------------

@superadmin_required
def console_overview_view(request):
    now = timezone.now()
    today = now.date()
    week_ago = today - datetime.timedelta(days=7)
    window_start = now - datetime.timedelta(days=30)
    prev_window_start = now - datetime.timedelta(days=60)

    total_users = User.objects.count()
    users_this_week = User.objects.filter(date_joined__date__gte=week_ago).count()
    users_percent = _percent_delta(users_this_week, max(0, total_users - users_this_week))

    active_sellers = Seller.objects.count()
    sellers_this_week = Seller.objects.filter(created_at__date__gte=week_ago).count()

    online_gmv_30d = Order.objects.filter(placed_at__gte=window_start).exclude(
        status=Order.Status.CANCELLED
    ).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    pos_gmv_30d = POSSale.objects.filter(sold_at__gte=window_start).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    gmv_30d = online_gmv_30d + pos_gmv_30d

    online_gmv_prev = Order.objects.filter(
        placed_at__gte=prev_window_start, placed_at__lt=window_start
    ).exclude(status=Order.Status.CANCELLED).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    pos_gmv_prev = POSSale.objects.filter(
        sold_at__gte=prev_window_start, sold_at__lt=window_start
    ).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    gmv_percent = _percent_delta(float(gmv_30d), float(online_gmv_prev + pos_gmv_prev))

    orders_today = Order.objects.filter(placed_at__date=today).count()
    orders_yesterday = Order.objects.filter(placed_at__date=today - datetime.timedelta(days=1)).count()
    orders_percent = _percent_delta(orders_today, orders_yesterday)

    chart = []
    for i in range(6, -1, -1):
        day = today - datetime.timedelta(days=i)
        count = Order.objects.filter(placed_at__date=day).count()
        chart.append({"label": day.strftime("%a"), "count": count})
    max_count = max((c["count"] for c in chart), default=0) or 1
    for c in chart:
        c["percent"] = round(c["count"] / max_count * 100) if max_count else 0

    activity = []
    for app in SellerApplication.objects.order_by("-submitted_at")[:8]:
        activity.append({"text": f"{app.business_name} submitted a seller application", "at": app.submitted_at})
        if app.reviewed_at:
            verb = "was approved as a seller" if app.status == SellerApplication.Status.APPROVED else "was rejected"
            activity.append({"text": f"{app.business_name} {verb}", "at": app.reviewed_at})
    for order in Order.objects.order_by("-placed_at")[:8]:
        activity.append({"text": f"Order {order.order_number} was placed for GH₵{order.total}", "at": order.placed_at})
        if order.status == Order.Status.CANCELLED:
            activity.append({"text": f"Order {order.order_number} was cancelled", "at": order.updated_at})
    activity.sort(key=lambda a: a["at"], reverse=True)

    pending_applications = SellerApplication.objects.filter(
        status=SellerApplication.Status.PENDING
    ).select_related("category").order_by("-submitted_at")[:5]

    ctx = _base_ctx("overview")
    ctx.update({
        "total_users": total_users,
        "users_percent": users_percent,
        "active_sellers": active_sellers,
        "sellers_this_week": sellers_this_week,
        "gmv_30d": gmv_30d,
        "gmv_percent": gmv_percent,
        "orders_today": orders_today,
        "orders_percent": orders_percent,
        "chart": chart,
        "activity": activity[:6],
        "pending_applications": pending_applications,
    })
    return render(request, "web/console_overview.html", ctx)


@superadmin_required
def console_overview_export_view(request):
    now = timezone.now()
    window_start = now - datetime.timedelta(days=30)
    rows = [
        ["Total users", User.objects.count()],
        ["Active sellers", Seller.objects.count()],
        [
            "GMV (30 days, GHS)",
            (Order.objects.filter(placed_at__gte=window_start).exclude(status=Order.Status.CANCELLED)
             .aggregate(t=Sum("total"))["t"] or Decimal("0.00"))
            + (POSSale.objects.filter(sold_at__gte=window_start).aggregate(t=Sum("total"))["t"] or Decimal("0.00")),
        ],
        ["Orders today", Order.objects.filter(placed_at__date=now.date()).count()],
        ["Pending seller applications", SellerApplication.objects.filter(status=SellerApplication.Status.PENDING).count()],
    ]
    return _csv_response("platform-overview.csv", ["Metric", "Value"], rows)


APPLICATION_TABS = {"pending", "approved", "rejected"}


@superadmin_required
def console_seller_applications_view(request):
    tab = request.GET.get("status", "pending")
    applications = SellerApplication.objects.select_related("category", "user").order_by("-submitted_at")
    if tab in APPLICATION_TABS:
        applications = applications.filter(status=tab)

    ctx = _base_ctx("seller-applications")
    ctx["applications"] = applications
    ctx["active_tab"] = tab
    return render(request, "web/console_seller_applications.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_seller_application_approve_view(request, application_id):
    application = get_object_or_404(SellerApplication, id=application_id)
    try:
        approve_application(
            application,
            reviewer_note=f"Approved via admin console by {request.user.full_name or request.user.email}",
        )
        messages.success(request, f"{application.business_name} approved as a seller.")
    except SellerApplicationError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-seller-applications")))


@superadmin_required
@require_http_methods(["POST"])
def console_seller_application_reject_view(request, application_id):
    application = get_object_or_404(SellerApplication, id=application_id)
    try:
        reject_application(
            application,
            reviewer_note=request.POST.get("reviewer_note")
            or f"Rejected via admin console by {request.user.full_name or request.user.email}",
        )
        messages.success(request, f"{application.business_name}'s application was rejected.")
    except SellerApplicationError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-seller-applications")))


@superadmin_required
def console_seller_applications_export_view(request):
    rows = [
        [app.business_name, app.category.name, app.phone, app.submitted_at.date(), app.status]
        for app in SellerApplication.objects.select_related("category").order_by("-submitted_at")
    ]
    return _csv_response("seller-applications.csv", ["Business", "Category", "Phone", "Submitted", "Status"], rows)


# -- Orders ---------------------------------------------------------------

@superadmin_required
def console_orders_view(request):
    query = request.GET.get("q", "").strip()
    orders = Order.objects.prefetch_related("items__product__seller").order_by("-placed_at")
    if query:
        orders = orders.filter(Q(order_number__icontains=query) | Q(delivery_recipient_name__icontains=query))

    orders = list(orders[:200])
    for order in orders:
        first_item = order.items.first()
        order.seller_name = first_item.product.seller.business_name if first_item else "—"

    ctx = _base_ctx("orders")
    ctx["orders"] = orders
    ctx["query"] = query
    return render(request, "web/console_orders.html", ctx)


@superadmin_required
def console_orders_export_view(request):
    rows = []
    for order in Order.objects.prefetch_related("items__product__seller").order_by("-placed_at"):
        first_item = order.items.first()
        seller_name = first_item.product.seller.business_name if first_item else "—"
        rows.append([
            order.order_number, order.delivery_recipient_name, seller_name,
            order.placed_at.date(), order.status, order.total,
        ])
    return _csv_response(
        "orders.csv", ["Order", "Customer", "Seller", "Date", "Status", "Total"], rows
    )


# -- Users ------------------------------------------------------------

USER_TABS = {"customers", "sellers", "admins"}


def _display_role(user) -> str:
    if user.is_superuser or user.is_staff:
        return "admin"
    if getattr(user, "seller_profile", None) is not None:
        return "seller"
    return "customer"


@superadmin_required
def console_users_view(request):
    tab = request.GET.get("type", "")
    users = User.objects.select_related("seller_profile").order_by("-date_joined")
    if tab == "customers":
        users = users.filter(seller_profile__isnull=True, is_staff=False, is_superuser=False)
    elif tab == "sellers":
        users = users.filter(seller_profile__isnull=False)
    elif tab == "admins":
        users = users.filter(Q(is_staff=True) | Q(is_superuser=True))

    users = list(users[:300])
    for user in users:
        user.display_role = _display_role(user)

    ctx = _base_ctx("users")
    ctx["users_list"] = users
    ctx["active_tab"] = tab
    return render(request, "web/console_users.html", ctx)


def _create_user_from_form(request):
    full_name = request.POST.get("full_name", "").strip()
    email = request.POST.get("email", "").strip().lower()
    phone = request.POST.get("phone", "").strip()
    password = request.POST.get("password", "")
    role = request.POST.get("role", User.Role.CUSTOMER)
    is_admin = bool(request.POST.get("is_admin"))

    if not email and not phone:
        messages.error(request, "Provide an email or phone number.")
        return None
    if role not in User.Role.values:
        role = User.Role.CUSTOMER
    if not password:
        messages.error(request, "Enter a password for this account.")
        return None
    try:
        validate_password(password)
    except DjangoValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return None
    if email and User.objects.filter(email=email).exists():
        messages.error(request, "A user with that email already exists.")
        return None
    if phone and User.objects.filter(phone=phone).exists():
        messages.error(request, "A user with that phone number already exists.")
        return None

    user = User(
        full_name=full_name,
        email=email or None,
        phone=phone or None,
        role=role,
        # Admin-created accounts are considered verified immediately - the
        # admin has already confirmed who this person is, so there's no need
        # to route them through the OTP signup flow.
        is_email_verified=bool(email),
        is_phone_verified=bool(phone),
        is_staff=is_admin,
        is_superuser=is_admin,
    )
    user.set_password(password)
    user.save()
    return user


@superadmin_required
def console_user_add_view(request):
    """Creates a customer or platform-admin account directly. Sellers still
    go through the seller-application approval flow (console_seller_application_approve_view),
    since that's what also creates the linked catalog.Seller storefront row."""
    if request.method == "POST":
        user = _create_user_from_form(request)
        if user is not None:
            messages.success(request, f"{user.full_name or user.email or user.phone} was added.")
            return redirect("web-console-users")

    ctx = _base_ctx("users")
    return render(request, "web/console_user_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_user_toggle_active_view(request, user_id):
    user = get_object_or_404(User, id=user_id)
    if user.is_superuser:
        messages.error(request, "You can't suspend a superadmin account.")
    else:
        user.is_active = not user.is_active
        user.save(update_fields=["is_active"])
        messages.success(request, f"{user.full_name or user.email or user.phone} was {'reactivated' if user.is_active else 'suspended'}.")
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-users")))


@superadmin_required
def console_users_export_view(request):
    rows = []
    for user in User.objects.select_related("seller_profile").order_by("-date_joined"):
        rows.append([
            user.full_name or "—", user.email or user.phone or "—", _display_role(user),
            user.date_joined.date(), "Active" if user.is_active else "Suspended",
        ])
    return _csv_response("users.csv", ["Name", "Contact", "Role", "Joined", "Status"], rows)


# -- Products ------------------------------------------------------------

@superadmin_required
def console_products_view(request):
    query = request.GET.get("q", "").strip()
    products = Product.objects.select_related("seller", "category").order_by("-created_at")
    if query:
        products = products.filter(name__icontains=query)

    ctx = _base_ctx("products")
    ctx["products"] = products[:300]
    ctx["query"] = query
    return render(request, "web/console_products.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_product_toggle_view(request, product_id):
    product = get_object_or_404(Product, id=product_id)
    product.is_active = not product.is_active
    product.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-products")))


@superadmin_required
def console_products_export_view(request):
    rows = [
        [p.name, p.seller.business_name, p.category.name, p.price, "Live" if p.is_active else "Inactive"]
        for p in Product.objects.select_related("seller", "category").order_by("-created_at")
    ]
    return _csv_response("products.csv", ["Product", "Seller", "Category", "Price", "Status"], rows)


# -- Payouts ---------------------------------------------------------------

PAYOUT_TABS = {"requested", "scheduled", "paid", "rejected"}


@superadmin_required
def console_payouts_view(request):
    tab = request.GET.get("status", "requested")
    payouts = Payout.objects.select_related("seller").order_by("-created_at")
    if tab in PAYOUT_TABS:
        payouts = payouts.filter(status=tab)

    ctx = _base_ctx("payouts")
    ctx["payouts"] = payouts
    ctx["active_tab"] = tab
    return render(request, "web/console_payouts.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_payout_schedule_view(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id)
    try:
        schedule_payout(
            payout,
            payout_date=request.POST.get("payout_date") or timezone.now().date(),
            admin_note=f"Scheduled via admin console by {request.user.full_name or request.user.email}",
        )
        messages.success(request, f"Payout for {payout.seller.business_name} scheduled.")
    except PayoutError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-payouts")))


@superadmin_required
@require_http_methods(["POST"])
def console_payout_mark_paid_view(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id)
    try:
        mark_payout_paid(payout)
        messages.success(request, f"Payout for {payout.seller.business_name} marked as paid.")
    except PayoutError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-payouts")))


@superadmin_required
@require_http_methods(["POST"])
def console_payout_reject_view(request, payout_id):
    payout = get_object_or_404(Payout, id=payout_id)
    try:
        reject_payout(
            payout,
            admin_note=request.POST.get("admin_note")
            or f"Rejected via admin console by {request.user.full_name or request.user.email}",
        )
        messages.success(request, f"Payout for {payout.seller.business_name} rejected.")
    except PayoutError as exc:
        messages.error(request, exc.message)
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-payouts")))


@superadmin_required
def console_payouts_export_view(request):
    rows = [
        [p.seller.business_name, p.amount, p.method, p.account_details, p.created_at.date(), p.status]
        for p in Payout.objects.select_related("seller").order_by("-created_at")
    ]
    return _csv_response(
        "payouts.csv", ["Seller", "Amount", "Method", "Account details", "Requested", "Status"], rows
    )


# -- Categories -------------------------------------------------------------

def _unique_slug(model, name: str, fallback: str = "item") -> str:
    base = slugify(name) or fallback
    slug = base
    suffix = 1
    while model.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


def _save_category_from_form(request, category=None):
    name = request.POST.get("name", "").strip()
    display_order = request.POST.get("display_order", "0").strip()
    commission_rate = request.POST.get("commission_rate", "").strip()

    if not name:
        messages.error(request, "Category name is required.")
        return None

    try:
        rate = Decimal(commission_rate) if commission_rate else Decimal("10.00")
    except InvalidOperation:
        messages.error(request, "Enter a valid commission rate.")
        return None

    if category is None:
        category = Category(slug=_unique_slug(Category, name, "category"))

    category.name = name
    if request.FILES.get("icon"):
        category.icon = request.FILES["icon"]
    category.commission_rate = rate
    try:
        category.display_order = int(display_order or 0)
    except ValueError:
        category.display_order = 0
    category.is_active = bool(request.POST.get("is_active", "1"))
    category.save()
    return category


@superadmin_required
def console_categories_view(request):
    query = request.GET.get("q", "").strip()
    categories = Category.objects.annotate(product_count=Count("products")).order_by("display_order", "name")
    if query:
        categories = categories.filter(name__icontains=query)

    ctx = _base_ctx("categories")
    ctx["categories"] = categories
    ctx["query"] = query
    return render(request, "web/console_categories.html", ctx)


@superadmin_required
def console_category_add_view(request):
    if request.method == "POST":
        category = _save_category_from_form(request)
        if category is not None:
            messages.success(request, f'"{category.name}" was added.')
            return redirect("web-console-categories")

    ctx = _base_ctx("categories")
    ctx["category"] = None
    return render(request, "web/console_category_form.html", ctx)


@superadmin_required
def console_category_edit_view(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    if request.method == "POST":
        saved = _save_category_from_form(request, category=category)
        if saved is not None:
            messages.success(request, f'"{saved.name}" was updated.')
            return redirect("web-console-category-edit", category_id=category.id)

    ctx = _base_ctx("categories")
    ctx["category"] = category
    ctx["subcategories"] = category.subcategories.order_by("display_order", "name")
    return render(request, "web/console_category_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_subcategory_add_view(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    name = request.POST.get("name", "").strip()
    if not name:
        messages.error(request, "Subcategory name is required.")
        return redirect("web-console-category-edit", category_id=category.id)

    if Subcategory.objects.filter(category=category, name__iexact=name).exists():
        messages.error(request, f'"{name}" already exists under {category.name}.')
        return redirect("web-console-category-edit", category_id=category.id)

    base_slug = slugify(name) or "subcategory"
    slug, suffix = base_slug, 1
    while Subcategory.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base_slug}-{suffix}"

    Subcategory.objects.create(category=category, name=name, slug=slug)
    messages.success(request, f'"{name}" was added under {category.name}.')
    return redirect("web-console-category-edit", category_id=category.id)


@superadmin_required
@require_http_methods(["POST"])
def console_subcategory_toggle_view(request, category_id, subcategory_id):
    subcategory = get_object_or_404(Subcategory, id=subcategory_id, category_id=category_id)
    subcategory.is_active = not subcategory.is_active
    subcategory.save(update_fields=["is_active"])
    return redirect("web-console-category-edit", category_id=category_id)


@superadmin_required
@require_http_methods(["POST"])
def console_subcategory_delete_view(request, category_id, subcategory_id):
    # Product.subcategory is SET_NULL, so deleting one just unlinks its
    # products rather than blocking - no protected-error fallback needed.
    subcategory = get_object_or_404(Subcategory, id=subcategory_id, category_id=category_id)
    subcategory.delete()
    messages.success(request, f'"{subcategory.name}" was deleted.')
    return redirect("web-console-category-edit", category_id=category_id)


@superadmin_required
@require_http_methods(["POST"])
def console_category_toggle_view(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    category.is_active = not category.is_active
    category.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-categories")))


@superadmin_required
@require_http_methods(["POST"])
def console_category_delete_view(request, category_id):
    category = get_object_or_404(Category, id=category_id)
    name = category.name
    try:
        category.delete()
        messages.success(request, f'"{name}" was deleted.')
    except ProtectedError:
        category.is_active = False
        category.save(update_fields=["is_active"])
        messages.error(request, f'"{name}" has existing products, so it was deactivated instead of deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-categories")))


# -- Delivery methods --------------------------------------------------------

def _save_delivery_method_from_form(request, delivery_method=None):
    name = request.POST.get("name", "").strip()
    code = request.POST.get("code", "").strip()
    price = request.POST.get("price", "").strip()
    eta_days_min = request.POST.get("eta_days_min", "").strip()
    eta_days_max = request.POST.get("eta_days_max", "").strip()

    if not name or not code:
        messages.error(request, "Name and code are required.")
        return None

    try:
        price = Decimal(price) if price else Decimal("0.00")
    except InvalidOperation:
        messages.error(request, "Enter a valid price.")
        return None

    try:
        eta_days_min = int(eta_days_min or 1)
        eta_days_max = int(eta_days_max or eta_days_min)
    except ValueError:
        messages.error(request, "Enter valid ETA days.")
        return None

    if eta_days_max < eta_days_min:
        messages.error(request, "Max ETA days can't be less than min ETA days.")
        return None

    if delivery_method is None:
        if DeliveryMethod.objects.filter(code=code).exists():
            messages.error(request, f'A delivery method with code "{code}" already exists.')
            return None
        delivery_method = DeliveryMethod(code=code)
    else:
        delivery_method.code = code

    delivery_method.name = name
    delivery_method.price = price
    delivery_method.eta_days_min = eta_days_min
    delivery_method.eta_days_max = eta_days_max
    delivery_method.is_active = bool(request.POST.get("is_active", "1"))
    delivery_method.save()
    return delivery_method


@superadmin_required
def console_delivery_methods_view(request):
    ctx = _base_ctx("delivery-methods")
    ctx["delivery_methods"] = DeliveryMethod.objects.order_by("price")
    return render(request, "web/console_delivery_methods.html", ctx)


@superadmin_required
def console_delivery_method_add_view(request):
    if request.method == "POST":
        delivery_method = _save_delivery_method_from_form(request)
        if delivery_method is not None:
            messages.success(request, f'"{delivery_method.name}" was added.')
            return redirect("web-console-delivery-methods")

    ctx = _base_ctx("delivery-methods")
    ctx["delivery_method"] = None
    return render(request, "web/console_delivery_method_form.html", ctx)


@superadmin_required
def console_delivery_method_edit_view(request, delivery_method_id):
    delivery_method = get_object_or_404(DeliveryMethod, id=delivery_method_id)
    if request.method == "POST":
        saved = _save_delivery_method_from_form(request, delivery_method=delivery_method)
        if saved is not None:
            messages.success(request, f'"{saved.name}" was updated.')
            return redirect("web-console-delivery-methods")

    ctx = _base_ctx("delivery-methods")
    ctx["delivery_method"] = delivery_method
    return render(request, "web/console_delivery_method_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_delivery_method_toggle_view(request, delivery_method_id):
    delivery_method = get_object_or_404(DeliveryMethod, id=delivery_method_id)
    delivery_method.is_active = not delivery_method.is_active
    delivery_method.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-delivery-methods")))


@superadmin_required
@require_http_methods(["POST"])
def console_delivery_method_delete_view(request, delivery_method_id):
    delivery_method = get_object_or_404(DeliveryMethod, id=delivery_method_id)
    name = delivery_method.name
    try:
        delivery_method.delete()
        messages.success(request, f'"{name}" was deleted.')
    except ProtectedError:
        delivery_method.is_active = False
        delivery_method.save(update_fields=["is_active"])
        messages.error(request, f'"{name}" has existing orders, so it was deactivated instead of deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-delivery-methods")))


# -- Brands -------------------------------------------------------------

def _save_brand_from_form(request, brand=None):
    name = request.POST.get("name", "").strip()

    if not name:
        messages.error(request, "Brand name is required.")
        return None

    if brand is None:
        brand = Brand(slug=_unique_slug(Brand, name, "brand"))

    brand.name = name
    if request.FILES.get("logo"):
        brand.logo = request.FILES["logo"]
    brand.is_active = bool(request.POST.get("is_active", "1"))
    brand.save()
    return brand


@superadmin_required
def console_brands_view(request):
    query = request.GET.get("q", "").strip()
    brands = Brand.objects.annotate(product_count=Count("products")).order_by("name")
    if query:
        brands = brands.filter(name__icontains=query)

    ctx = _base_ctx("brands")
    ctx["brands"] = brands
    ctx["query"] = query
    return render(request, "web/console_brands.html", ctx)


@superadmin_required
def console_brand_add_view(request):
    if request.method == "POST":
        brand = _save_brand_from_form(request)
        if brand is not None:
            messages.success(request, f'"{brand.name}" was added.')
            return redirect("web-console-brands")

    ctx = _base_ctx("brands")
    ctx["brand"] = None
    return render(request, "web/console_brand_form.html", ctx)


@superadmin_required
def console_brand_edit_view(request, brand_id):
    brand = get_object_or_404(Brand, id=brand_id)
    if request.method == "POST":
        saved = _save_brand_from_form(request, brand=brand)
        if saved is not None:
            messages.success(request, f'"{saved.name}" was updated.')
            return redirect("web-console-brands")

    ctx = _base_ctx("brands")
    ctx["brand"] = brand
    return render(request, "web/console_brand_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_brand_toggle_view(request, brand_id):
    brand = get_object_or_404(Brand, id=brand_id)
    brand.is_active = not brand.is_active
    brand.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-brands")))


@superadmin_required
@require_http_methods(["POST"])
def console_brand_delete_view(request, brand_id):
    # Product.brand is SET_NULL, so deleting one just unlinks its products.
    brand = get_object_or_404(Brand, id=brand_id)
    brand.delete()
    messages.success(request, f'"{brand.name}" was deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-brands")))


# -- Banners --------------------------------------------------------------

def _parse_datetime_local(value: str):
    if not value:
        return None
    parsed = datetime.datetime.fromisoformat(value)
    return timezone.make_aware(parsed) if timezone.is_naive(parsed) else parsed


def _save_banner_from_form(request, banner=None):
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Banner title is required.")
        return None

    try:
        active_from = _parse_datetime_local(request.POST.get("active_from", "").strip())
        active_to = _parse_datetime_local(request.POST.get("active_to", "").strip())
    except ValueError:
        messages.error(request, "Enter valid start/end dates.")
        return None

    try:
        display_order = int(request.POST.get("display_order") or 0)
    except ValueError:
        display_order = 0

    if banner is None:
        banner = Banner()

    banner.title = title
    banner.subtitle = request.POST.get("subtitle", "").strip()
    if request.FILES.get("image"):
        banner.image = request.FILES["image"]
    banner.cta_label = request.POST.get("cta_label", "").strip()
    banner.cta_link = request.POST.get("cta_link", "").strip()
    banner.active_from = active_from
    banner.active_to = active_to
    banner.display_order = display_order
    banner.is_active = bool(request.POST.get("is_active", "1"))
    banner.save()
    return banner


@superadmin_required
def console_banners_view(request):
    ctx = _base_ctx("banners")
    ctx["banners"] = Banner.objects.order_by("display_order")
    return render(request, "web/console_banners.html", ctx)


@superadmin_required
def console_banner_add_view(request):
    if request.method == "POST":
        banner = _save_banner_from_form(request)
        if banner is not None:
            messages.success(request, f'"{banner.title}" was added.')
            return redirect("web-console-banners")

    ctx = _base_ctx("banners")
    ctx["banner"] = None
    return render(request, "web/console_banner_form.html", ctx)


@superadmin_required
def console_banner_edit_view(request, banner_id):
    banner = get_object_or_404(Banner, id=banner_id)
    if request.method == "POST":
        saved = _save_banner_from_form(request, banner=banner)
        if saved is not None:
            messages.success(request, f'"{saved.title}" was updated.')
            return redirect("web-console-banners")

    ctx = _base_ctx("banners")
    ctx["banner"] = banner
    return render(request, "web/console_banner_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_banner_toggle_view(request, banner_id):
    banner = get_object_or_404(Banner, id=banner_id)
    banner.is_active = not banner.is_active
    banner.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-banners")))


@superadmin_required
@require_http_methods(["POST"])
def console_banner_delete_view(request, banner_id):
    banner = get_object_or_404(Banner, id=banner_id)
    banner.delete()
    messages.success(request, f'"{banner.title}" was deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-banners")))


# -- Collections ------------------------------------------------------------

def _save_collection_from_form(request, collection=None):
    title = request.POST.get("title", "").strip()
    if not title:
        messages.error(request, "Collection title is required.")
        return None

    try:
        display_order = int(request.POST.get("display_order") or 0)
    except ValueError:
        display_order = 0

    linked_category = None
    linked_category_id = request.POST.get("linked_category_id")
    if linked_category_id:
        linked_category = Category.objects.filter(id=linked_category_id).first()

    if collection is None:
        collection = Collection(slug=_unique_slug(Collection, title, "collection"))

    collection.title = title
    if request.FILES.get("banner_image"):
        collection.banner_image = request.FILES["banner_image"]
    collection.linked_category = linked_category
    collection.display_order = display_order
    collection.is_active = bool(request.POST.get("is_active", "1"))
    collection.save()

    slugs = [s.strip() for s in request.POST.get("product_slugs", "").splitlines() if s.strip()]
    if slugs:
        collection.products.set(Product.objects.filter(slug__in=slugs))
    else:
        collection.products.clear()

    return collection


@superadmin_required
def console_collections_view(request):
    ctx = _base_ctx("collections")
    ctx["collections"] = Collection.objects.annotate(product_count=Count("products")).order_by("display_order")
    return render(request, "web/console_collections.html", ctx)


@superadmin_required
def console_collection_add_view(request):
    if request.method == "POST":
        collection = _save_collection_from_form(request)
        if collection is not None:
            messages.success(request, f'"{collection.title}" was added.')
            return redirect("web-console-collections")

    ctx = _base_ctx("collections")
    ctx["collection"] = None
    ctx["categories"] = Category.objects.filter(is_active=True)
    return render(request, "web/console_collection_form.html", ctx)


@superadmin_required
def console_collection_edit_view(request, collection_id):
    collection = get_object_or_404(Collection, id=collection_id)
    if request.method == "POST":
        saved = _save_collection_from_form(request, collection=collection)
        if saved is not None:
            messages.success(request, f'"{saved.title}" was updated.')
            return redirect("web-console-collections")

    ctx = _base_ctx("collections")
    ctx["collection"] = collection
    ctx["categories"] = Category.objects.filter(is_active=True)
    ctx["current_product_slugs"] = "\n".join(collection.products.values_list("slug", flat=True))
    return render(request, "web/console_collection_form.html", ctx)


@superadmin_required
@require_http_methods(["POST"])
def console_collection_toggle_view(request, collection_id):
    collection = get_object_or_404(Collection, id=collection_id)
    collection.is_active = not collection.is_active
    collection.save(update_fields=["is_active"])
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-collections")))


@superadmin_required
@require_http_methods(["POST"])
def console_collection_delete_view(request, collection_id):
    collection = get_object_or_404(Collection, id=collection_id)
    collection.delete()
    messages.success(request, f'"{collection.title}" was deleted.')
    return redirect(_safe_redirect_target(request, request.POST.get("next"), reverse("web-console-collections")))
