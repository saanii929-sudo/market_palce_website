import json
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalog.models import Product
from pos.models import Customer, Discount, Employee, POSSale
from pos.services import POSError, complete_sale, find_product

from .views import _seller_order_qs, subscription_required

@subscription_required
def seller_employees_view(request, seller):
    return render(request, "web/seller_employees.html", {
        "active_nav": "employees",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "employees": seller.employees.all(),
    })


def _save_employee_from_form(request, seller, employee=None):
    full_name = request.POST.get("full_name", "").strip()
    role = request.POST.get("role", Employee.Role.CASHIER)
    pin = request.POST.get("pin", "").strip()

    if not full_name:
        messages.error(request, "Employee name is required.")
        return None
    if pin and (not pin.isdigit() or len(pin) < 4):
        messages.error(request, "PIN must be at least 4 digits.")
        return None
    if employee is None and not pin:
        messages.error(request, "Set a PIN so this employee can clock in at the till.")
        return None

    if employee is None:
        employee = Employee(seller=seller)

    employee.full_name = full_name
    employee.phone = request.POST.get("phone", "").strip()
    employee.email = request.POST.get("email", "").strip()
    employee.role = role
    employee.is_active = bool(request.POST.get("is_active", "1"))
    try:
        employee.base_salary = Decimal(request.POST.get("base_salary") or "0")
    except InvalidOperation:
        employee.base_salary = Decimal("0.00")
    if pin:
        employee.set_pin(pin)
    employee.save()
    return employee


@subscription_required
def seller_employee_add_view(request, seller):
    if request.method == "POST":
        employee = _save_employee_from_form(request, seller)
        if employee is not None:
            messages.success(request, f'"{employee.full_name}" was added to your team.')
            return redirect("web-seller-employees")

    return render(request, "web/seller_employee_form.html", {
        "active_nav": "employees",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "employee": None,
        "roles": Employee.Role.choices,
    })


@subscription_required
def seller_employee_edit_view(request, seller, employee_id):
    employee = get_object_or_404(Employee, id=employee_id, seller=seller)
    if request.method == "POST":
        saved = _save_employee_from_form(request, seller, employee=employee)
        if saved is not None:
            messages.success(request, f'"{saved.full_name}" was updated.')
            return redirect("web-seller-employees")

    return render(request, "web/seller_employee_form.html", {
        "active_nav": "employees",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "employee": employee,
        "roles": Employee.Role.choices,
    })


@subscription_required
@require_http_methods(["POST"])
def seller_employee_toggle_view(request, seller, employee_id):
    employee = get_object_or_404(Employee, id=employee_id, seller=seller)
    employee.is_active = not employee.is_active
    employee.save(update_fields=["is_active"])
    return redirect("web-seller-employees")


@subscription_required
@require_http_methods(["POST"])
def seller_employee_delete_view(request, seller, employee_id):
    employee = get_object_or_404(Employee, id=employee_id, seller=seller)
    employee.delete()
    messages.success(request, "Employee removed.")
    return redirect("web-seller-employees")


def _clocked_in_employee(request, seller):
    employee_id = request.session.get(f"pos_employee_{seller.id}")
    if not employee_id:
        return None
    return Employee.objects.filter(id=employee_id, seller=seller, is_active=True).first()


@subscription_required
def pos_terminal_view(request, seller):
    employee = _clocked_in_employee(request, seller)
    if employee is None:
        return render(request, "web/pos_terminal.html", {
            "active_nav": "pos",
            "seller": seller,
            "products_count": Product.objects.filter(seller=seller).count(),
            "orders_count": _seller_order_qs(seller).count(),
            "employee": None,
            "employees": seller.employees.filter(is_active=True),
        })

    recent_products = list(
        Product.objects.filter(seller=seller, is_active=True)
        .prefetch_related("variants", "images")
        .order_by("-sold_count")[:12]
    )
    for product in recent_products:
        product.variants_json = json.dumps([
            {"id": v.id, "size": v.size, "color": v.color, "stock_qty": v.stock_qty, "in_stock": v.in_stock}
            for v in product.variants.all()
        ])

    return render(request, "web/pos_terminal.html", {
        "active_nav": "pos",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "employee": employee,
        "recent_products": recent_products,
        "customers": seller.customers.all(),
        "discounts": seller.discounts.filter(is_active=True),
    })


@subscription_required
@require_http_methods(["POST"])
def pos_clock_in_view(request, seller):
    employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), seller=seller, is_active=True)
    pin = request.POST.get("pin", "").strip()
    if not employee.check_pin(pin):
        messages.error(request, "Incorrect PIN.")
        return redirect("web-pos-terminal")
    request.session[f"pos_employee_{seller.id}"] = employee.id
    return redirect("web-pos-terminal")


@subscription_required
@require_http_methods(["POST"])
def pos_clock_out_view(request, seller):
    request.session.pop(f"pos_employee_{seller.id}", None)
    return redirect("web-pos-terminal")


@subscription_required
def pos_lookup_view(request, seller):
    code = request.GET.get("code", "").strip()
    query = request.GET.get("q", "").strip()

    def _variants(product):
        return [
            {"id": v.id, "size": v.size, "color": v.color, "stock_qty": v.stock_qty, "in_stock": v.in_stock}
            for v in product.variants.all()
        ]

    if code:
        product = find_product(seller, code)
        if product is None:
            return JsonResponse({"found": False})
        photo = product.images.first()
        return JsonResponse({
            "found": True,
            "product": {
                "id": product.id,
                "name": product.name,
                "price": str(product.price),
                "stock_qty": product.stock_qty,
                "image": photo.resolved_url if photo else None,
                "variants": _variants(product),
            },
        })

    if query:
        products = Product.objects.filter(seller=seller, is_active=True, name__icontains=query)[:10]
        return JsonResponse({
            "results": [
                {
                    "id": p.id, "name": p.name, "price": str(p.price), "stock_qty": p.stock_qty,
                    "image": p.images.first().resolved_url if p.images.first() else None,
                    "variants": _variants(p),
                }
                for p in products
            ]
        })

    return JsonResponse({"found": False, "results": []})


@subscription_required
@require_http_methods(["POST"])
def pos_checkout_view(request, seller):
    employee = _clocked_in_employee(request, seller)
    if employee is None:
        messages.error(request, "Clock in with your PIN before taking a sale.")
        return redirect("web-pos-terminal")

    product_ids = request.POST.getlist("product_id")
    variant_ids = request.POST.getlist("variant_id")
    qtys = request.POST.getlist("qty")
    cart_lines = [
        {"product_id": int(pid), "variant_id": int(vid) if vid else None, "qty": int(qty)}
        for pid, vid, qty in zip(product_ids, variant_ids, qtys)
        if pid and qty
    ]

    def _decimal(field, default="0"):
        try:
            return Decimal(request.POST.get(field) or default)
        except InvalidOperation:
            return Decimal(default)

    customer = None
    customer_id = request.POST.get("customer_id")
    if customer_id:
        customer = Customer.objects.filter(id=customer_id, seller=seller).first()

    discount = None
    discount_id = request.POST.get("discount_id")
    if discount_id:
        discount = Discount.objects.filter(id=discount_id, seller=seller, is_active=True).first()

    try:
        sale = complete_sale(
            seller=seller,
            employee=employee,
            cart_lines=cart_lines,
            payment_method=request.POST.get("payment_method", POSSale.PaymentMethod.CASH),
            customer_name=request.POST.get("customer_name", "").strip(),
            customer=customer,
            discount=discount,
            discount_amount=_decimal("discount_amount"),
            amount_tendered=_decimal("amount_tendered", "") if request.POST.get("amount_tendered") else None,
        )
    except POSError as exc:
        messages.error(request, exc.message)
        return redirect("web-pos-terminal")

    return redirect("web-pos-receipt", receipt_number=sale.receipt_number)


@subscription_required
def pos_receipt_view(request, seller, receipt_number):
    sale = get_object_or_404(POSSale, receipt_number=receipt_number, seller=seller)
    return render(request, "web/pos_receipt.html", {
        "seller": seller,
        "sale": sale,
    })


@subscription_required
def pos_sales_history_view(request, seller):
    tab = request.GET.get("range", "")
    sales = POSSale.objects.filter(seller=seller).select_related("employee").prefetch_related("items__product")
    today = timezone.now().date()
    if tab == "today":
        sales = sales.filter(sold_at__date=today)
    elif tab == "week":
        sales = sales.filter(sold_at__date__gte=today - timedelta(days=7))
    elif tab == "month":
        sales = sales.filter(sold_at__year=today.year, sold_at__month=today.month)

    return render(request, "web/pos_sales_history.html", {
        "active_nav": "pos-sales",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "sales": sales,
        "active_tab": tab,
    })


def _shift_month(date_value, months):
    total = date_value.month - 1 + months
    year = date_value.year + total // 12
    month = total % 12 + 1
    return date_value.replace(year=year, month=month, day=1)


@subscription_required
def pos_reports_view(request, seller):
    now = timezone.now()
    today = now.date()
    sales_qs = POSSale.objects.filter(seller=seller, status=POSSale.Status.COMPLETED)

    today_total = sales_qs.filter(sold_at__date=today).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    today_count = sales_qs.filter(sold_at__date=today).count()
    month_total = sales_qs.filter(sold_at__year=today.year, sold_at__month=today.month).aggregate(t=Sum("total"))[
        "t"
    ] or Decimal("0.00")
    year_total = sales_qs.filter(sold_at__year=today.year).aggregate(t=Sum("total"))["t"] or Decimal("0.00")

    range_param = request.GET.get("range", "week")
    chart = []
    if range_param == "year":
        for i in range(4, -1, -1):
            year = today.year - i
            amt = sales_qs.filter(sold_at__year=year).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
            chart.append({"label": str(year), "amount": amt})
    elif range_param == "month":
        for i in range(5, -1, -1):
            month_start = _shift_month(today.replace(day=1), -i)
            amt = sales_qs.filter(sold_at__year=month_start.year, sold_at__month=month_start.month).aggregate(
                t=Sum("total")
            )["t"] or Decimal("0.00")
            chart.append({"label": month_start.strftime("%b"), "amount": amt})
    else:
        range_param = "week"
        for i in range(6, -1, -1):
            day = today - timedelta(days=i)
            amt = sales_qs.filter(sold_at__date=day).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
            chart.append({"label": day.strftime("%a"), "amount": amt})

    max_amount = max((c["amount"] for c in chart), default=Decimal("0.00")) or Decimal("1.00")
    for c in chart:
        c["percent"] = round(float(c["amount"] / max_amount) * 100) if max_amount else 0

    payment_breakdown = []
    total_all = sales_qs.aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    for code, label in POSSale.PaymentMethod.choices:
        amount = sales_qs.filter(payment_method=code).aggregate(t=Sum("total"))["t"] or Decimal("0.00")
        percent = round(float(amount / total_all) * 100) if total_all else 0
        payment_breakdown.append({"label": label, "amount": amount, "percent": percent})

    top_products = (
        Product.objects.filter(seller=seller, pos_sale_items__isnull=False)
        .distinct()
        .order_by("-sold_count")[:5]
    )

    return render(request, "web/pos_reports.html", {
        "active_nav": "pos-reports",
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
        "today_total": today_total,
        "today_count": today_count,
        "month_total": month_total,
        "year_total": year_total,
        "chart": chart,
        "range_param": range_param,
        "payment_breakdown": payment_breakdown,
        "top_products": top_products,
    })
