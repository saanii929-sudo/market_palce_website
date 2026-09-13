import csv
import datetime
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from catalog.models import Product
from pos.models import Customer, Invoice, InvoicePayment, POSSale
from pos.services import (
    POSError,
    build_invoice_lines_from_pos_sale,
    create_invoice,
    record_invoice_payment,
    send_invoice,
    void_invoice,
)

from .views import _seller_order_qs, seller_required


def _base_ctx(seller, active_nav):
    return {
        "active_nav": active_nav,
        "seller": seller,
        "products_count": Product.objects.filter(seller=seller).count(),
        "orders_count": _seller_order_qs(seller).count(),
    }


def _parse_date(value, default=None):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return default


INVOICE_TAB_STATUSES = {
    "draft": [Invoice.Status.DRAFT],
    "sent": [Invoice.Status.SENT],
    "unpaid": [Invoice.Status.SENT, Invoice.Status.PARTIALLY_PAID],
    "paid": [Invoice.Status.PAID],
    "void": [Invoice.Status.VOID],
}


@seller_required
def seller_invoices_view(request, seller):
    tab = request.GET.get("status", "")
    invoices = seller.invoices.select_related("customer").prefetch_related("items")
    if tab in INVOICE_TAB_STATUSES:
        invoices = invoices.filter(status__in=INVOICE_TAB_STATUSES[tab])

    ctx = _base_ctx(seller, "invoices")
    ctx["invoices"] = invoices
    ctx["active_tab"] = tab
    ctx["outstanding_total"] = sum(
        (inv.balance_due for inv in invoices if inv.status not in (Invoice.Status.PAID, Invoice.Status.VOID)),
        Decimal("0.00"),
    )
    return render(request, "web/seller_invoices.html", ctx)


@seller_required
def seller_invoice_add_view(request, seller):
    if request.method == "POST":
        customer = None
        customer_id = request.POST.get("customer_id")
        if customer_id:
            customer = Customer.objects.filter(id=customer_id, seller=seller).first()

        product_ids = request.POST.getlist("product_id")
        descriptions = request.POST.getlist("description")
        qtys = request.POST.getlist("qty")
        unit_prices = request.POST.getlist("unit_price")
        line_items = [
            {"product_id": pid or None, "description": desc, "qty": qty, "unit_price": price}
            for pid, desc, qty, price in zip(product_ids, descriptions, qtys, unit_prices)
        ]

        try:
            tax_rate = Decimal(request.POST.get("tax_rate") or "0")
        except InvalidOperation:
            tax_rate = Decimal("0.00")
        try:
            discount_amount = Decimal(request.POST.get("discount_amount") or "0")
        except InvalidOperation:
            discount_amount = Decimal("0.00")

        linked_sale = None
        linked_sale_id = request.POST.get("linked_pos_sale_id")
        if linked_sale_id:
            linked_sale = POSSale.objects.filter(id=linked_sale_id, seller=seller).first()

        try:
            invoice = create_invoice(
                seller=seller,
                customer=customer,
                customer_name=request.POST.get("customer_name", "").strip(),
                customer_phone=request.POST.get("customer_phone", "").strip(),
                customer_email=request.POST.get("customer_email", "").strip(),
                customer_address=request.POST.get("customer_address", "").strip(),
                issue_date=_parse_date(request.POST.get("issue_date"), timezone.now().date()),
                due_date=_parse_date(request.POST.get("due_date")),
                tax_rate=tax_rate,
                discount_amount=discount_amount,
                notes=request.POST.get("notes", "").strip(),
                terms=request.POST.get("terms", "").strip(),
                line_items=line_items,
                linked_pos_sale=linked_sale,
            )
            messages.success(request, f"Invoice {invoice.invoice_number} created.")
            return redirect("web-seller-invoice-detail", invoice_id=invoice.id)
        except POSError as exc:
            messages.error(request, exc.message)

    prefill_items = []
    prefill_customer = None
    pos_sale_id = request.GET.get("from_pos_sale")
    linked_sale = None
    if pos_sale_id:
        linked_sale = POSSale.objects.filter(id=pos_sale_id, seller=seller).first()
        if linked_sale:
            prefill_items = build_invoice_lines_from_pos_sale(linked_sale)
            prefill_customer = linked_sale.customer

    prefill_items_json = [
        {
            "product_id": line["product_id"],
            "description": line["description"],
            "qty": str(line["qty"]),
            "unit_price": str(line["unit_price"]),
        }
        for line in prefill_items
    ]

    ctx = _base_ctx(seller, "invoices")
    ctx["customers"] = seller.customers.all()
    ctx["products"] = Product.objects.filter(seller=seller)
    ctx["prefill_items_json"] = prefill_items_json
    ctx["prefill_customer"] = prefill_customer
    ctx["linked_sale"] = linked_sale
    return render(request, "web/seller_invoice_form.html", ctx)


@seller_required
def seller_invoice_detail_view(request, seller, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, seller=seller)
    ctx = _base_ctx(seller, "invoices")
    ctx["invoice"] = invoice
    ctx["items"] = invoice.items.all()
    ctx["payments"] = invoice.payments.all()
    return render(request, "web/seller_invoice_detail.html", ctx)


@seller_required
@require_http_methods(["POST"])
def seller_invoice_payment_add_view(request, seller, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, seller=seller)
    try:
        amount = Decimal(request.POST.get("amount") or "0")
    except InvalidOperation:
        amount = Decimal("0.00")

    try:
        record_invoice_payment(
            invoice, amount=amount, method=request.POST.get("method", InvoicePayment.Method.CASH),
            paid_on=_parse_date(request.POST.get("paid_on"), timezone.now().date()),
            note=request.POST.get("note", "").strip(),
        )
        messages.success(request, "Payment recorded.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-invoice-detail", invoice_id=invoice.id)


@seller_required
@require_http_methods(["POST"])
def seller_invoice_send_view(request, seller, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, seller=seller)
    try:
        send_invoice(invoice, request)
        messages.success(request, f"Invoice sent to {invoice.customer_email}.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-invoice-detail", invoice_id=invoice.id)


@seller_required
@require_http_methods(["POST"])
def seller_invoice_void_view(request, seller, invoice_id):
    invoice = get_object_or_404(Invoice, id=invoice_id, seller=seller)
    try:
        void_invoice(invoice)
        messages.success(request, f"Invoice {invoice.invoice_number} voided.")
    except POSError as exc:
        messages.error(request, exc.message)
    return redirect("web-seller-invoice-detail", invoice_id=invoice.id)


@seller_required
def seller_invoices_export_view(request, seller):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{seller.slug}-invoices.csv"'
    writer = csv.writer(response)
    status_labels = dict(Invoice.Status.choices)
    writer.writerow(["Invoice", "Customer", "Issue date", "Due date", "Status", "Total", "Paid", "Balance due"])
    for invoice in seller.invoices.all():
        writer.writerow([
            invoice.invoice_number, invoice.customer_name, invoice.issue_date, invoice.due_date or "",
            status_labels.get(invoice.display_status, invoice.display_status),
            invoice.total, invoice.amount_paid, invoice.balance_due,
        ])
    return response
