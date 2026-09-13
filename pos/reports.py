"""Pure calculation helpers for the seller's financial reports - no models are
written here, only aggregated read-side numbers for the P&L statement."""

from decimal import Decimal

from django.db.models import DecimalField, ExpressionWrapper, F, Sum

from orders.models import Order, OrderItem

from .models import Expense, POSReturn, POSReturnItem, POSSale, POSSaleItem


def _line_value_sum(qs, price_field: str, qty_field: str = "qty") -> Decimal:
    aggregate = qs.aggregate(
        total=Sum(
            ExpressionWrapper(F(price_field) * F(qty_field), output_field=DecimalField(max_digits=12, decimal_places=2))
        )
    )
    return aggregate["total"] or Decimal("0.00")


def compute_pnl(seller, start_date, end_date) -> dict:
    online_items = OrderItem.objects.filter(
        product__seller=seller,
        order__placed_at__date__gte=start_date,
        order__placed_at__date__lte=end_date,
    ).exclude(order__status=Order.Status.CANCELLED)

    online_revenue = _line_value_sum(online_items, "unit_price")
    online_cogs = _line_value_sum(online_items, "product__cost_price")

    pos_sales = POSSale.objects.filter(seller=seller, sold_at__date__gte=start_date, sold_at__date__lte=end_date)
    pos_gross_sales = pos_sales.aggregate(t=Sum("total"))["t"] or Decimal("0.00")

    pos_items = POSSaleItem.objects.filter(sale__in=pos_sales)
    pos_cogs_gross = _line_value_sum(pos_items, "product__cost_price")

    returns_qs = POSReturn.objects.filter(
        sale__seller=seller, created_at__date__gte=start_date, created_at__date__lte=end_date
    )
    returns_total = returns_qs.aggregate(t=Sum("refund_amount"))["t"] or Decimal("0.00")
    returned_cogs = _line_value_sum(
        POSReturnItem.objects.filter(pos_return__in=returns_qs), "sale_item__product__cost_price", "qty"
    )

    pos_revenue = pos_gross_sales - returns_total
    pos_cogs = pos_cogs_gross - returned_cogs

    total_revenue = online_revenue + pos_revenue
    total_cogs = online_cogs + pos_cogs
    gross_profit = total_revenue - total_cogs

    expenses = Expense.objects.filter(seller=seller, incurred_on__gte=start_date, incurred_on__lte=end_date)
    total_expenses = expenses.aggregate(t=Sum("amount"))["t"] or Decimal("0.00")
    expenses_by_category = list(
        expenses.values("category").annotate(total=Sum("amount")).order_by("-total")
    )

    net_income = gross_profit - total_expenses

    gross_margin_ratio = (gross_profit / total_revenue) if total_revenue else Decimal("0.00")
    break_even_revenue = (
        (total_expenses / gross_margin_ratio).quantize(Decimal("0.01"))
        if gross_margin_ratio > 0
        else None
    )

    return {
        "online_revenue": online_revenue,
        "pos_revenue": pos_revenue,
        "returns_total": returns_total,
        "total_revenue": total_revenue,
        "online_cogs": online_cogs,
        "pos_cogs": pos_cogs,
        "total_cogs": total_cogs,
        "gross_profit": gross_profit,
        "gross_margin_percent": round(float(gross_margin_ratio) * 100, 1),
        "total_expenses": total_expenses,
        "expenses_by_category": expenses_by_category,
        "net_income": net_income,
        "break_even_revenue": break_even_revenue,
    }
