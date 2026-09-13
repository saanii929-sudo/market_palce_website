from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from catalog.models import Product

from .models import (
    Customer,
    Discount,
    Employee,
    Expense,
    Invoice,
    InvoiceItem,
    InvoicePayment,
    PaySlip,
    PayRun,
    POSReturn,
    POSReturnItem,
    POSSale,
    POSSaleItem,
    ProductBatch,
    PurchaseOrder,
)


class POSError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def find_product(seller, code: str) -> Product | None:
    code = (code or "").strip()
    if not code:
        return None
    return Product.objects.filter(seller=seller).filter(Q(barcode=code) | Q(sku__iexact=code)).first()


@transaction.atomic
def complete_sale(
    *,
    seller,
    employee: Employee | None,
    cart_lines: list[dict],
    payment_method: str,
    customer_name: str = "",
    customer: Customer | None = None,
    discount: Discount | None = None,
    discount_amount: Decimal = Decimal("0.00"),
    amount_tendered: Decimal | None = None,
) -> POSSale:
    """cart_lines: [{"product_id": int, "qty": int}, ...]"""
    if not cart_lines:
        raise POSError("Cart is empty.")

    products = {
        p.id: p
        for p in Product.objects.select_for_update().filter(
            id__in=[line["product_id"] for line in cart_lines], seller=seller
        )
    }

    subtotal = Decimal("0.00")
    resolved_lines = []
    for line in cart_lines:
        product = products.get(line["product_id"])
        if product is None:
            raise POSError("One of the scanned products no longer exists in your store.")
        qty = int(line["qty"])
        if qty <= 0:
            continue
        if product.stock_qty < qty:
            raise POSError(f'Not enough stock for "{product.name}" ({product.stock_qty} left).')
        line_total = product.price * qty
        subtotal += line_total
        resolved_lines.append((product, qty))

    if not resolved_lines:
        raise POSError("Cart is empty.")

    if discount is not None:
        discount_amount = discount.compute_amount(subtotal)
    else:
        discount_amount = discount_amount or Decimal("0.00")
    total = max(Decimal("0.00"), subtotal - discount_amount)

    sale = POSSale.objects.create(
        seller=seller,
        employee=employee,
        customer=customer,
        customer_name=customer_name or (customer.full_name if customer else ""),
        discount=discount,
        payment_method=payment_method,
        subtotal=subtotal,
        discount_amount=discount_amount,
        total=total,
        amount_tendered=amount_tendered,
    )

    for product, qty in resolved_lines:
        POSSaleItem.objects.create(sale=sale, product=product, qty=qty, unit_price=product.price)
        product.stock_qty = product.stock_qty - qty
        product.sold_count = product.sold_count + qty
        product.save(update_fields=["stock_qty", "sold_count"])

    return sale


@transaction.atomic
def process_return(
    *, sale: POSSale, lines: list[dict], reason: str = "", processed_by: Employee | None = None, restock: bool = True
) -> POSReturn:
    """lines: [{"sale_item_id": int, "qty": int}, ...]"""
    if not lines:
        raise POSError("Select at least one item to return.")

    discount_ratio = (sale.discount_amount / sale.subtotal) if sale.subtotal else Decimal("0.00")
    refund_amount = Decimal("0.00")
    pos_return = POSReturn.objects.create(sale=sale, processed_by=processed_by, reason=reason, restocked=restock)

    any_line = False
    for line in lines:
        qty = int(line.get("qty") or 0)
        if qty <= 0:
            continue
        sale_item = sale.items.select_for_update().get(id=line["sale_item_id"])
        if qty > sale_item.returnable_qty:
            raise POSError(f'Cannot return more than {sale_item.returnable_qty} of "{sale_item.product.name}".')

        line_refund = (sale_item.unit_price * qty) * (Decimal("1.00") - discount_ratio)
        line_refund = line_refund.quantize(Decimal("0.01"))
        refund_amount += line_refund

        POSReturnItem.objects.create(pos_return=pos_return, sale_item=sale_item, qty=qty)
        sale_item.returned_qty += qty
        sale_item.save(update_fields=["returned_qty"])

        if restock:
            product = sale_item.product
            product.stock_qty += qty
            product.sold_count = max(0, product.sold_count - qty)
            product.save(update_fields=["stock_qty", "sold_count"])
        any_line = True

    if not any_line:
        pos_return.delete()
        raise POSError("Select at least one item to return.")

    pos_return.refund_amount = refund_amount
    pos_return.save(update_fields=["refund_amount"])

    sale.refunded_amount += refund_amount
    total_qty = sum(i.qty for i in sale.items.all())
    returned_qty = sum(i.returned_qty for i in sale.items.all())
    sale.status = POSSale.Status.REFUNDED if returned_qty >= total_qty else POSSale.Status.PARTIALLY_REFUNDED
    sale.save(update_fields=["refunded_amount", "status"])

    return pos_return


@transaction.atomic
def receive_purchase_order(po: PurchaseOrder) -> PurchaseOrder:
    if po.status != PurchaseOrder.Status.PENDING:
        raise POSError("Only pending purchase orders can be received.")

    for item in po.items.select_related("product").all():
        product = item.product
        existing_stock = product.stock_qty
        existing_cost = product.cost_price or Decimal("0.00")
        new_total_qty = existing_stock + item.qty
        if new_total_qty > 0:
            product.cost_price = (
                (existing_stock * existing_cost) + (item.qty * item.unit_cost)
            ) / new_total_qty
        product.stock_qty = new_total_qty
        product.save(update_fields=["stock_qty", "cost_price"])

        ProductBatch.objects.create(
            product=product,
            supplier=po.supplier,
            purchase_order_item=item,
            batch_code=f"{po.reference}-{item.id}",
            qty_received=item.qty,
            qty_remaining=item.qty,
            unit_cost=item.unit_cost,
            expiry_date=item.expiry_date,
        )

    po.status = PurchaseOrder.Status.RECEIVED
    po.received_at = timezone.now()
    po.save(update_fields=["status", "received_at"])
    return po


@transaction.atomic
def write_off_batch(batch: ProductBatch, seller) -> Expense:
    if batch.is_written_off or batch.qty_remaining <= 0:
        raise POSError("This batch has already been written off.")

    write_off_qty = batch.qty_remaining
    write_off_value = (write_off_qty * batch.unit_cost).quantize(Decimal("0.01"))

    product = batch.product
    product.stock_qty = max(0, product.stock_qty - write_off_qty)
    product.save(update_fields=["stock_qty"])

    batch.qty_remaining = 0
    batch.is_written_off = True
    batch.save(update_fields=["qty_remaining", "is_written_off"])

    return Expense.objects.create(
        seller=seller,
        category=Expense.Category.INVENTORY_WRITE_OFF,
        description=f"Write-off: {write_off_qty} x {product.name} (expired batch {batch.batch_code})",
        amount=write_off_value,
    )


@transaction.atomic
def run_payroll(seller, period_start, period_end) -> PayRun:
    pay_run = PayRun.objects.create(seller=seller, period_start=period_start, period_end=period_end)
    for employee in seller.employees.filter(is_active=True):
        PaySlip.objects.create(pay_run=pay_run, employee=employee, base_salary=employee.base_salary)
    return pay_run


@transaction.atomic
def mark_payroll_paid(pay_run: PayRun) -> Expense:
    if pay_run.status != PayRun.Status.DRAFT:
        raise POSError("This pay run has already been paid.")

    total = pay_run.total_net_pay
    pay_run.status = PayRun.Status.PAID
    pay_run.paid_at = timezone.now()
    pay_run.save(update_fields=["status", "paid_at"])

    return Expense.objects.create(
        seller=pay_run.seller,
        category=Expense.Category.PAYROLL,
        description=f"Payroll {pay_run.period_start} to {pay_run.period_end}",
        amount=total,
        incurred_on=pay_run.paid_at.date(),
    )


# -- Invoicing --------------------------------------------------------------

@transaction.atomic
def create_invoice(
    *,
    seller,
    customer: Customer | None,
    customer_name: str,
    customer_phone: str = "",
    customer_email: str = "",
    customer_address: str = "",
    issue_date=None,
    due_date=None,
    tax_rate: Decimal = Decimal("0.00"),
    discount_amount: Decimal = Decimal("0.00"),
    notes: str = "",
    terms: str = "",
    line_items: list[dict] | None = None,
    linked_pos_sale: POSSale | None = None,
) -> Invoice:
    """line_items: [{"description": str, "qty": Decimal, "unit_price": Decimal, "product_id": int|None}, ...]"""
    if not customer_name and customer is None:
        raise POSError("Add a customer name for this invoice.")
    if not line_items:
        raise POSError("Add at least one line item to the invoice.")

    invoice = Invoice.objects.create(
        seller=seller,
        customer=customer,
        customer_name=customer_name or customer.full_name,
        customer_phone=customer_phone or (customer.phone if customer else ""),
        customer_email=customer_email or (customer.email if customer else ""),
        customer_address=customer_address,
        issue_date=issue_date or timezone.now().date(),
        due_date=due_date,
        tax_rate=tax_rate or Decimal("0.00"),
        discount_amount=discount_amount or Decimal("0.00"),
        notes=notes,
        terms=terms,
        linked_pos_sale=linked_pos_sale,
    )

    for line in line_items:
        qty = Decimal(str(line.get("qty") or "0"))
        unit_price = Decimal(str(line.get("unit_price") or "0"))
        description = (line.get("description") or "").strip()
        if qty <= 0 or not description:
            continue
        InvoiceItem.objects.create(
            invoice=invoice, product_id=line.get("product_id") or None,
            description=description, qty=qty, unit_price=unit_price,
        )

    if not invoice.items.exists():
        invoice.delete()
        raise POSError("Add at least one valid line item to the invoice.")

    invoice.recompute_totals()
    return invoice


def build_invoice_lines_from_pos_sale(sale: POSSale) -> list[dict]:
    return [
        {"description": item.product.name, "qty": item.qty, "unit_price": item.unit_price, "product_id": item.product_id}
        for item in sale.items.all()
    ]


@transaction.atomic
def record_invoice_payment(invoice: Invoice, *, amount: Decimal, method: str, paid_on=None, note: str = "") -> InvoicePayment:
    if invoice.status == Invoice.Status.VOID:
        raise POSError("Cannot record a payment against a void invoice.")
    if amount <= 0:
        raise POSError("Enter a valid payment amount.")

    payment = InvoicePayment.objects.create(
        invoice=invoice, amount=amount, method=method, paid_on=paid_on or timezone.now().date(), note=note
    )

    invoice.amount_paid += amount
    if invoice.amount_paid >= invoice.total:
        invoice.status = Invoice.Status.PAID
    elif invoice.amount_paid > 0:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    invoice.save(update_fields=["amount_paid", "status"])
    return payment


def send_invoice(invoice: Invoice, request) -> None:
    if not invoice.customer_email:
        raise POSError("This customer has no email address on file.")

    from django.core.mail import send_mail
    from django.urls import reverse

    link = request.build_absolute_uri(reverse("web-seller-invoice-detail", args=[invoice.id]))
    send_mail(
        subject=f"Invoice {invoice.invoice_number} from {invoice.seller.business_name}",
        message=(
            f"Hi {invoice.customer_name},\n\n"
            f"Here is your invoice {invoice.invoice_number} for GH₵{invoice.total}, due "
            f"{invoice.due_date or 'on receipt'}.\n\nView it here: {link}\n\n"
            f"Thank you for your business."
        ),
        from_email=None,
        recipient_list=[invoice.customer_email],
        fail_silently=True,
    )

    if invoice.status == Invoice.Status.DRAFT:
        invoice.status = Invoice.Status.SENT
        invoice.save(update_fields=["status"])


@transaction.atomic
def void_invoice(invoice: Invoice) -> Invoice:
    if invoice.status == Invoice.Status.PAID:
        raise POSError("A fully paid invoice cannot be voided.")
    invoice.status = Invoice.Status.VOID
    invoice.save(update_fields=["status"])
    return invoice
