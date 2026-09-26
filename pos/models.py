from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

from catalog.models import Product, ProductVariant, Seller
from core.models import TimeStampedModel


def generate_invoice_number(pk: int) -> str:
    return f"INV-{100000 + pk}"


class Employee(TimeStampedModel):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        CASHIER = "cashier", "Cashier"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="employees")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CASHIER)
    pin_hash = models.CharField(max_length=128, blank=True)
    base_salary = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    hire_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.get_role_display()})"

    def set_pin(self, raw_pin: str) -> None:
        self.pin_hash = make_password(raw_pin)

    def check_pin(self, raw_pin: str) -> bool:
        return bool(self.pin_hash) and check_password(raw_pin, self.pin_hash)


class Customer(TimeStampedModel):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="customers")
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["full_name"]

    def __str__(self):
        return self.full_name


class Discount(TimeStampedModel):
    class Type(models.TextChoices):
        PERCENTAGE = "percentage", "Percentage"
        FIXED = "fixed", "Fixed amount"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="discounts")
    name = models.CharField(max_length=100)
    code = models.CharField(max_length=40, blank=True)
    discount_type = models.CharField(max_length=20, choices=Type.choices, default=Type.PERCENTAGE)
    value = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def compute_amount(self, subtotal: Decimal) -> Decimal:
        if self.discount_type == self.Type.PERCENTAGE:
            return min(subtotal, (subtotal * self.value / Decimal("100")).quantize(Decimal("0.01")))
        return min(subtotal, self.value)


class POSSale(TimeStampedModel):
    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        MOBILE_MONEY = "mobile_money", "Mobile Money"

    class Status(models.TextChoices):
        COMPLETED = "completed", "Completed"
        PARTIALLY_REFUNDED = "partially_refunded", "Partially refunded"
        REFUNDED = "refunded", "Refunded"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="pos_sales")
    employee = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sales"
    )
    customer = models.ForeignKey(
        Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sales"
    )
    discount = models.ForeignKey(
        Discount, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sales"
    )
    receipt_number = models.CharField(max_length=20, unique=True, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2)
    amount_tendered = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    sold_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sold_at"]
        indexes = [models.Index(fields=["seller", "sold_at"])]

    def __str__(self):
        return self.receipt_number or f"Sale #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.receipt_number:
            self.receipt_number = f"POS-{100000 + self.pk}"
            super().save(update_fields=["receipt_number"])

    @property
    def change_due(self) -> Decimal | None:
        if self.amount_tendered is None:
            return None
        return max(Decimal("0.00"), self.amount_tendered - self.total)

    @property
    def net_total(self) -> Decimal:
        return self.total - self.refunded_amount


class POSSaleItem(TimeStampedModel):
    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="pos_sale_items")
    variant = models.ForeignKey(
        ProductVariant, on_delete=models.SET_NULL, null=True, blank=True, related_name="pos_sale_items"
    )
    qty = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    returned_qty = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.qty} x {self.product.name}"

    @property
    def line_total(self) -> Decimal:
        return self.unit_price * self.qty

    @property
    def returnable_qty(self) -> int:
        return self.qty - self.returned_qty


class POSReturn(TimeStampedModel):
    sale = models.ForeignKey(POSSale, on_delete=models.CASCADE, related_name="returns")
    processed_by = models.ForeignKey(
        Employee, on_delete=models.SET_NULL, null=True, blank=True, related_name="processed_returns"
    )
    reason = models.CharField(max_length=255, blank=True)
    refund_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    restocked = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Return for {self.sale.receipt_number}"


class POSReturnItem(TimeStampedModel):
    pos_return = models.ForeignKey(POSReturn, on_delete=models.CASCADE, related_name="items")
    sale_item = models.ForeignKey(POSSaleItem, on_delete=models.CASCADE, related_name="return_items")
    qty = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.qty} x {self.sale_item.product.name}"


class Supplier(TimeStampedModel):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="suppliers")
    name = models.CharField(max_length=150)
    contact_name = models.CharField(max_length=150, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class PurchaseOrder(TimeStampedModel):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RECEIVED = "received", "Received"
        CANCELLED = "cancelled", "Cancelled"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="purchase_orders")
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="purchase_orders")
    reference = models.CharField(max_length=20, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes = models.CharField(max_length=255, blank=True)
    ordered_at = models.DateTimeField(default=timezone.now)
    received_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-ordered_at"]

    def __str__(self):
        return self.reference or f"PO #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.reference:
            self.reference = f"PO-{10000 + self.pk}"
            super().save(update_fields=["reference"])

    @property
    def total_cost(self) -> Decimal:
        return sum((item.line_cost for item in self.items.all()), Decimal("0.00"))


class PurchaseOrderItem(TimeStampedModel):
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="purchase_order_items")
    qty = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f"{self.qty} x {self.product.name}"

    @property
    def line_cost(self) -> Decimal:
        return self.unit_cost * self.qty


class ProductBatch(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="batches")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True, related_name="batches")
    purchase_order_item = models.ForeignKey(
        PurchaseOrderItem, on_delete=models.SET_NULL, null=True, blank=True, related_name="batches"
    )
    batch_code = models.CharField(max_length=60, blank=True)
    qty_received = models.PositiveIntegerField()
    qty_remaining = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    expiry_date = models.DateField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    is_written_off = models.BooleanField(default=False)

    class Meta:
        ordering = ["expiry_date"]

    def __str__(self):
        return self.batch_code or f"Batch #{self.pk}"

    @property
    def is_expired(self) -> bool:
        return bool(self.expiry_date) and self.expiry_date < timezone.now().date()

    @property
    def is_expiring_soon(self) -> bool:
        if not self.expiry_date or self.is_expired:
            return False
        return (self.expiry_date - timezone.now().date()).days <= 30


class WarehouseStock(TimeStampedModel):
    """Backroom/overstock inventory for a product, tracked separately from
    Product.stock_qty (what's actually listed as sellable on the
    storefront). Only meaningful while the seller has Seller.has_warehouse
    enabled - see pos.services.receive_into_warehouse and
    import_from_warehouse for the two operations that move stock in and out
    of it."""

    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="warehouse_stock")
    qty_on_hand = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"WarehouseStock({self.product.name}, {self.qty_on_hand})"


class WarehouseTransfer(TimeStampedModel):
    """Audit trail entry for one 'import from warehouse' action - how many
    units of a product moved from the warehouse onto the storefront, and
    who did it. Purely a log; the actual balances live on WarehouseStock
    and Product.stock_qty."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="warehouse_transfers")
    qty = models.PositiveIntegerField()
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"WarehouseTransfer({self.product.name}, {self.qty})"


class Expense(TimeStampedModel):
    class Category(models.TextChoices):
        RENT = "rent", "Rent"
        UTILITIES = "utilities", "Utilities"
        PAYROLL = "payroll", "Payroll"
        INVENTORY_WRITE_OFF = "inventory_write_off", "Inventory write-off"
        MARKETING = "marketing", "Marketing"
        OTHER = "other", "Other"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="expenses")
    category = models.CharField(max_length=30, choices=Category.choices, default=Category.OTHER)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    incurred_on = models.DateField(default=timezone.now)

    class Meta:
        ordering = ["-incurred_on"]

    def __str__(self):
        return f"{self.get_category_display()}: GHS {self.amount}"


class PayRun(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PAID = "paid", "Paid"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="pay_runs")
    period_start = models.DateField()
    period_end = models.DateField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-period_start"]

    def __str__(self):
        return f"Pay run {self.period_start} – {self.period_end}"

    @property
    def total_net_pay(self) -> Decimal:
        return sum((slip.net_pay for slip in self.payslips.all()), Decimal("0.00"))


class PaySlip(TimeStampedModel):
    pay_run = models.ForeignKey(PayRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="payslips")
    base_salary = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    deductions = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["employee__full_name"]

    def __str__(self):
        return f"{self.employee.full_name} — {self.pay_run}"

    @property
    def net_pay(self) -> Decimal:
        return self.base_salary + self.bonus - self.deductions


class Invoice(TimeStampedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SENT = "sent", "Sent"
        PARTIALLY_PAID = "partially_paid", "Partially paid"
        PAID = "paid", "Paid"
        OVERDUE = "overdue", "Overdue"
        VOID = "void", "Void"

    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="invoices")
    customer = models.ForeignKey(Customer, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices")

    customer_name = models.CharField(max_length=150)
    customer_phone = models.CharField(max_length=20, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_address = models.CharField(max_length=255, blank=True)

    invoice_number = models.CharField(max_length=20, unique=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)

    issue_date = models.DateField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)

    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))

    notes = models.TextField(blank=True)
    terms = models.TextField(blank=True)

    linked_pos_sale = models.ForeignKey(
        POSSale, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoices"
    )

    class Meta:
        ordering = ["-issue_date", "-id"]

    def __str__(self):
        return self.invoice_number or f"Invoice #{self.pk}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.invoice_number:
            self.invoice_number = generate_invoice_number(self.pk)
            super().save(update_fields=["invoice_number"])

    def recompute_totals(self, save: bool = True) -> None:
        subtotal = sum((item.line_total for item in self.items.all()), Decimal("0.00"))
        taxable = max(Decimal("0.00"), subtotal - self.discount_amount)
        tax_amount = (taxable * self.tax_rate / Decimal("100")).quantize(Decimal("0.01"))
        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total = taxable + tax_amount
        if save:
            self.save(update_fields=["subtotal", "tax_amount", "total"])

    @property
    def balance_due(self) -> Decimal:
        return self.total - self.amount_paid

    @property
    def is_overdue(self) -> bool:
        if self.status in (self.Status.PAID, self.Status.VOID):
            return False
        return bool(self.due_date) and self.due_date < timezone.now().date() and self.balance_due > 0

    @property
    def display_status(self) -> str:
        if self.status not in (self.Status.PAID, self.Status.VOID) and self.is_overdue:
            return self.Status.OVERDUE
        return self.status


class InvoiceItem(TimeStampedModel):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True, related_name="invoice_items"
    )
    description = models.CharField(max_length=255)
    qty = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("1.00"))
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.qty} x {self.description}"

    @property
    def line_total(self) -> Decimal:
        return self.qty * self.unit_price


class InvoicePayment(TimeStampedModel):
    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Card"
        MOBILE_MONEY = "mobile_money", "Mobile Money"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"

    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.CASH)
    paid_on = models.DateField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-paid_on", "-id"]

    def __str__(self):
        return f"GHS {self.amount} on {self.paid_on}"
