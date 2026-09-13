import csv
import datetime
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.models import User
from catalog.models import Product, Seller
from orders.models import Order
from pos.models import POSSale
from sellers.models import SellerApplication
from sellers.services import SellerApplicationError, approve_application, reject_application

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
    }


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


# -- Seller applications --------------------------------------------------

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
