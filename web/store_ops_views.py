import csv
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalog.models import Product
from pos.models import (
    Customer,
    Discount,
    Expense,
    PaySlip,
    PayRun,
    POSReturn,
    POSSale,
    ProductBatch,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from pos.reports import compute_pnl
from pos.services import POSError, mark_payroll_paid, process_return, receive_purchase_order, run_payroll, write_off_batch

from .views import _seller_order_qs, subscription_required


def _base_ctx(seller, active_nav):
    return {
        "active_nav": active_nav,
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
    }


def _csv_response(filename, header, rows):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    writer = csv.writer(response)
    writer.writerow(header)
    for row in rows:
        writer.writerow(row)
    return response


def _parse_date(value, default):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


@subscription_required
def seller_suppliers_view(request, seller):
    ctx = _base_ctx(seller, "suppliers")
    ctx["suppliers"] = seller.suppliers.all()
    return render(request, "web/seller_suppliers.html", ctx)


@subscription_required
def seller_supplier_add_view(request, seller):
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if not name:
            messages.error(request, "Supplier name is required.")
        else:
            Supplier.objects.create(
                seller=seller, name=name,
                contact_name=request.POST.get("contact_name", "").strip(),
                phone=request.POST.get("phone", "").strip(),
                email=request.POST.get("email", "").strip(),
                address=request.POST.get("address", "").strip(),
            )
            messages.success(request, f'"{name}" was added as a supplier.')
            return redirect("web-seller-suppliers")

    ctx = _base_ctx(seller, "suppliers")
    return render(request, "web/seller_supplier_form.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_supplier_toggle_view(request, seller, supplier_id):
    supplier = get_object_or_404(Supplier, id=supplier_id, seller=seller)
    supplier.is_active = not supplier.is_active
    supplier.save(update_fields=["is_active"])
    return redirect("web-seller-suppliers")


@subscription_required
def seller_purchase_orders_view(request, seller):
    ctx = _base_ctx(seller, "purchase-orders")
    ctx["purchase_orders"] = seller.purchase_orders.select_related("supplier").prefetch_related("items")
    return render(request, "web/seller_purchase_orders.html", ctx)


@subscription_required
def seller_purchase_order_add_view(request, seller):
    if request.method == "POST":
        supplier = get_object_or_404(Supplier, id=request.POST.get("supplier_id"), seller=seller)
        product_ids = request.POST.getlist("product_id")
        qtys = request.POST.getlist("qty")
        costs = request.POST.getlist("unit_cost")
        expiries = request.POST.getlist("expiry_date")

        po = PurchaseOrder.objects.create(seller=seller, supplier=supplier, notes=request.POST.get("notes", "").strip())
        line_count = 0
        for pid, qty, cost, expiry in zip(product_ids, qtys, costs, expiries):
            if not pid or not qty or not cost:
                continue
            product = get_object_or_404(Product, id=pid, seller=seller)
            PurchaseOrderItem.objects.create(
                purchase_order=po, product=product, qty=int(qty), unit_cost=Decimal(cost),
                expiry_date=_parse_date(expiry, None),
            )
            line_count += 1

        if line_count == 0:
            po.delete()
            messages.error(request, "Add at least one product line to the purchase order.")
        else:
            messages.success(request, f"Purchase order {po.reference} created.")
            return redirect("web-seller-purchase-orders")

    ctx = _base_ctx(seller, "purchase-orders")
    ctx["suppliers"] = seller.suppliers.filter(is_active=True)
    ctx["products"] = Product.objects.filter(seller=seller)
    return render(request, "web/seller_purchase_order_form.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_purchase_order_receive_view(request, seller, po_id):
    po = get_object_or_404(PurchaseOrder, id=po_id, seller=seller)
    try:
        receive_purchase_order(po)
        messages.success(request, f"{po.reference} received — stock and costs updated.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-purchase-orders")


@subscription_required
@require_http_methods(["POST"])
def seller_purchase_order_cancel_view(request, seller, po_id):
    po = get_object_or_404(PurchaseOrder, id=po_id, seller=seller, status=PurchaseOrder.Status.PENDING)
    po.status = PurchaseOrder.Status.CANCELLED
    po.save(update_fields=["status"])
    return redirect("web-seller-purchase-orders")


LOW_STOCK_THRESHOLD = 10


@subscription_required
def seller_inventory_view(request, seller):
    view = request.GET.get("view", "stock")
    ctx = _base_ctx(seller, "inventory")
    ctx["view"] = view

    if view == "expiry":
        ctx["batches"] = (
            ProductBatch.objects.filter(product__seller=seller, is_written_off=False, qty_remaining__gt=0)
            .select_related("product")
            .order_by("expiry_date")
        )
    else:
        products = list(Product.objects.filter(seller=seller).select_related("category"))
        for product in products:
            product.stock_value = product.stock_qty * product.cost_price
        ctx["products"] = products
        ctx["low_stock_threshold"] = LOW_STOCK_THRESHOLD
        ctx["inventory_value"] = sum((p.stock_value for p in products), Decimal("0.00"))

    return render(request, "web/seller_inventory.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_batch_write_off_view(request, seller, batch_id):
    batch = get_object_or_404(ProductBatch, id=batch_id, product__seller=seller)
    try:
        write_off_batch(batch, seller)
        messages.success(request, f"Batch {batch.batch_code} written off and logged as an expense.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect(f"{reverse('web-seller-inventory')}?view=expiry")


@subscription_required
def seller_inventory_export_view(request, seller):
    products = Product.objects.filter(seller=seller).select_related("category")
    rows = [
        [p.name, p.sku, p.category.name, p.stock_qty, p.cost_price, p.price, p.stock_qty * p.cost_price]
        for p in products
    ]
    return _csv_response(
        f"{seller.slug}-inventory.csv",
        ["Product", "SKU", "Category", "Stock qty", "Unit cost (GHS)", "Sale price (GHS)", "Stock value (GHS)"],
        rows,
    )


@subscription_required
def seller_expenses_view(request, seller):
    ctx = _base_ctx(seller, "expenses")
    ctx["expenses"] = seller.expenses.all()
    ctx["categories"] = Expense.Category.choices
    return render(request, "web/seller_expenses.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_expense_add_view(request, seller):
    try:
        amount = Decimal(request.POST.get("amount") or "0")
    except InvalidOperation:
        amount = Decimal("0.00")

    if amount <= 0:
        messages.error(request, "Enter a valid expense amount.")
    else:
        Expense.objects.create(
            seller=seller,
            category=request.POST.get("category", Expense.Category.OTHER),
            description=request.POST.get("description", "").strip(),
            amount=amount,
            incurred_on=_parse_date(request.POST.get("incurred_on"), timezone.now().date()),
        )
        messages.success(request, "Expense logged.")
    return redirect("web-seller-expenses")


# -- Payroll ------------------------------------------------------------

@subscription_required
def seller_payroll_view(request, seller):
    ctx = _base_ctx(seller, "payroll")
    ctx["pay_runs"] = seller.pay_runs.prefetch_related("payslips__employee")
    return render(request, "web/seller_payroll.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_payroll_run_view(request, seller):
    period_start = _parse_date(request.POST.get("period_start"), None)
    period_end = _parse_date(request.POST.get("period_end"), None)
    if not period_start or not period_end:
        messages.error(request, "Choose a valid pay period.")
        return redirect("web-seller-payroll")

    pay_run = run_payroll(seller, period_start, period_end)
    messages.success(request, f"Pay run created for {pay_run.payslips.count()} employee(s).")
    return redirect("web-seller-payroll-detail", pay_run_id=pay_run.id)


@subscription_required
def seller_payroll_detail_view(request, seller, pay_run_id):
    pay_run = get_object_or_404(PayRun, id=pay_run_id, seller=seller)
    ctx = _base_ctx(seller, "payroll")
    ctx["pay_run"] = pay_run
    ctx["payslips"] = pay_run.payslips.select_related("employee")
    return render(request, "web/seller_payroll_detail.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_payslip_update_view(request, seller, payslip_id):
    payslip = get_object_or_404(PaySlip, id=payslip_id, pay_run__seller=seller, pay_run__status=PayRun.Status.DRAFT)

    def _dec(field):
        try:
            return Decimal(request.POST.get(field) or "0")
        except InvalidOperation:
            return Decimal("0.00")

    payslip.bonus = _dec("bonus")
    payslip.deductions = _dec("deductions")
    payslip.save(update_fields=["bonus", "deductions"])
    return redirect("web-seller-payroll-detail", pay_run_id=payslip.pay_run_id)


@subscription_required
@require_http_methods(["POST"])
def seller_payroll_mark_paid_view(request, seller, pay_run_id):
    pay_run = get_object_or_404(PayRun, id=pay_run_id, seller=seller)
    try:
        mark_payroll_paid(pay_run)
        messages.success(request, f"Pay run marked paid — GH₵{pay_run.total_net_pay} logged as a payroll expense.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-payroll-detail", pay_run_id=pay_run.id)


@subscription_required
def seller_payroll_export_view(request, seller):
    rows = []
    for pay_run in seller.pay_runs.prefetch_related("payslips__employee"):
        for slip in pay_run.payslips.all():
            rows.append([
                pay_run.period_start, pay_run.period_end, pay_run.get_status_display(),
                slip.employee.full_name, slip.base_salary, slip.bonus, slip.deductions, slip.net_pay,
            ])
    return _csv_response(
        f"{seller.slug}-payroll.csv",
        ["Period start", "Period end", "Status", "Employee", "Base salary", "Bonus", "Deductions", "Net pay"],
        rows,
    )


@subscription_required
def seller_discounts_view(request, seller):
    ctx = _base_ctx(seller, "discounts")
    ctx["discounts"] = seller.discounts.all()
    return render(request, "web/seller_discounts.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_discount_add_view(request, seller):
    name = request.POST.get("name", "").strip()
    try:
        value = Decimal(request.POST.get("value") or "0")
    except InvalidOperation:
        value = Decimal("0.00")

    if not name or value <= 0:
        messages.error(request, "Give the discount a name and a positive value.")
    else:
        Discount.objects.create(
            seller=seller, name=name, code=request.POST.get("code", "").strip(),
            discount_type=request.POST.get("discount_type", Discount.Type.PERCENTAGE), value=value,
        )
        messages.success(request, f'Discount "{name}" created.')
    return redirect("web-seller-discounts")


@subscription_required
@require_http_methods(["POST"])
def seller_discount_toggle_view(request, seller, discount_id):
    discount = get_object_or_404(Discount, id=discount_id, seller=seller)
    discount.is_active = not discount.is_active
    discount.save(update_fields=["is_active"])
    return redirect("web-seller-discounts")


@subscription_required
@require_http_methods(["POST"])
def seller_discount_delete_view(request, seller, discount_id):
    discount = get_object_or_404(Discount, id=discount_id, seller=seller)
    discount.delete()
    return redirect("web-seller-discounts")


@subscription_required
def seller_customers_view(request, seller):
    ctx = _base_ctx(seller, "customers")
    ctx["customers"] = seller.customers.all()
    return render(request, "web/seller_customers.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_customer_add_view(request, seller):
    full_name = request.POST.get("full_name", "").strip()
    if not full_name:
        messages.error(request, "Customer name is required.")
    else:
        Customer.objects.create(
            seller=seller, full_name=full_name,
            phone=request.POST.get("phone", "").strip(),
            email=request.POST.get("email", "").strip(),
        )
        messages.success(request, f'"{full_name}" was added.')
    return redirect("web-seller-customers")


@subscription_required
def seller_customer_detail_view(request, seller, customer_id):
    customer = get_object_or_404(Customer, id=customer_id, seller=seller)
    ctx = _base_ctx(seller, "customers")
    ctx["customer"] = customer
    ctx["sales"] = customer.pos_sales.prefetch_related("items__product")
    ctx["lifetime_spend"] = customer.pos_sales.aggregate(t=Sum("total"))["t"] or Decimal("0.00")
    return render(request, "web/seller_customer_detail.html", ctx)


@subscription_required
def seller_customers_export_view(request, seller):
    rows = []
    for customer in seller.customers.all():
        spend = customer.pos_sales.aggregate(t=Sum("total"))["t"] or Decimal("0.00")
        rows.append([customer.full_name, customer.phone, customer.email, customer.pos_sales.count(), spend])
    return _csv_response(
        f"{seller.slug}-customers.csv",
        ["Name", "Phone", "Email", "Purchases", "Lifetime spend (GHS)"],
        rows,
    )


@subscription_required
def seller_returns_view(request, seller):
    ctx = _base_ctx(seller, "pos-returns")
    ctx["returns"] = POSReturn.objects.filter(sale__seller=seller).select_related("sale", "processed_by")
    receipt = request.GET.get("receipt", "").strip()
    sale = None
    if receipt:
        sale = POSSale.objects.filter(seller=seller, receipt_number=receipt).prefetch_related("items__product").first()
        if sale is None:
            messages.error(request, f'No sale found for receipt "{receipt}".')
    ctx["sale"] = sale
    ctx["receipt_query"] = receipt
    return render(request, "web/seller_returns.html", ctx)


@subscription_required
@require_http_methods(["POST"])
def seller_return_process_view(request, seller):
    from .pos_views import _clocked_in_employee

    sale = get_object_or_404(POSSale, id=request.POST.get("sale_id"), seller=seller)
    item_ids = request.POST.getlist("sale_item_id")
    qtys = request.POST.getlist("return_qty")
    lines = [
        {"sale_item_id": int(iid), "qty": int(qty)}
        for iid, qty in zip(item_ids, qtys)
        if iid and qty and int(qty) > 0
    ]

    try:
        process_return(
            sale=sale, lines=lines, reason=request.POST.get("reason", "").strip(),
            processed_by=_clocked_in_employee(request, seller),
            restock=bool(request.POST.get("restock")),
        )
        messages.success(request, f"Return processed for {sale.receipt_number}.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-returns")


@subscription_required
def seller_reports_hub_view(request, seller):
    ctx = _base_ctx(seller, "reports")
    return render(request, "web/seller_reports_hub.html", ctx)


@subscription_required
def seller_pnl_view(request, seller):
    today = timezone.now().date()
    start_date = _parse_date(request.GET.get("start"), today.replace(day=1))
    end_date = _parse_date(request.GET.get("end"), today)

    figures = compute_pnl(seller, start_date, end_date)
    category_labels = dict(Expense.Category.choices)
    for row in figures["expenses_by_category"]:
        row["label"] = category_labels.get(row["category"], row["category"])

    ctx = _base_ctx(seller, "reports")
    ctx.update(figures)
    ctx["start_date"] = start_date
    ctx["end_date"] = end_date
    return render(request, "web/seller_pnl.html", ctx)


@subscription_required
def seller_pnl_export_view(request, seller):
    today = timezone.now().date()
    start_date = _parse_date(request.GET.get("start"), today.replace(day=1))
    end_date = _parse_date(request.GET.get("end"), today)
    figures = compute_pnl(seller, start_date, end_date)

    rows = [
        ["Period", f"{start_date} to {end_date}"],
        [],
        ["Online sales revenue", figures["online_revenue"]],
        ["In-store sales revenue (net of returns)", figures["pos_revenue"]],
        ["Total revenue", figures["total_revenue"]],
        [],
        ["Online cost of goods sold", figures["online_cogs"]],
        ["In-store cost of goods sold", figures["pos_cogs"]],
        ["Total cost of goods sold (COGS)", figures["total_cogs"]],
        [],
        ["Gross profit", figures["gross_profit"]],
        ["Gross margin %", figures["gross_margin_percent"]],
        [],
        ["Total operating expenses", figures["total_expenses"]],
    ]
    for row in figures["expenses_by_category"]:
        rows.append([f"  - {dict(Expense.Category.choices).get(row['category'], row['category'])}", row["total"]])
    rows += [
        [],
        ["Net income (profit) / deficit (loss)", figures["net_income"]],
        ["Estimated break-even revenue", figures["break_even_revenue"] or "N/A"],
    ]
    return _csv_response(f"{seller.slug}-profit-and-loss.csv", ["Line item", "Amount (GHS)"], rows)


@subscription_required
def seller_suppliers_export_view(request, seller):
    rows = []
    for po in seller.purchase_orders.select_related("supplier").prefetch_related("items"):
        for item in po.items.all():
            rows.append([
                po.reference, po.supplier.name, po.get_status_display(), po.ordered_at.date(),
                item.product.name, item.qty, item.unit_cost, item.line_cost, item.expiry_date or "",
            ])
    return _csv_response(
        f"{seller.slug}-purchase-orders.csv",
        ["PO reference", "Supplier", "Status", "Ordered on", "Product", "Qty", "Unit cost", "Line cost", "Expiry date"],
        rows,
    )


@subscription_required
def seller_discounts_export_view(request, seller):
    rows = []
    for discount in seller.discounts.all():
        sales = discount.pos_sales.all()
        rows.append([
            discount.name, discount.code, discount.get_discount_type_display(), discount.value,
            "Active" if discount.is_active else "Inactive", sales.count(),
            sales.aggregate(t=Sum("discount_amount"))["t"] or Decimal("0.00"),
        ])
    return _csv_response(
        f"{seller.slug}-discounts.csv",
        ["Name", "Code", "Type", "Value", "Status", "Times used", "Total discounted (GHS)"],
        rows,
    )
