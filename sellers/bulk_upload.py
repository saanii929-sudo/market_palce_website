"""Bulk product upload from a seller-supplied CSV - validated row by row
against the same rules the single-product form enforces (see
web.views._save_product_from_form), so a CSV can never sneak in a product
the manual form would have rejected. One bad row never fails the batch:
it's recorded in the job's error_report and every other valid row still
gets created."""

import csv
import io
import secrets
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils.text import slugify

from catalog.models import Category, Product

from .models import BulkUploadJob

REQUIRED_COLUMNS = ["name", "category", "price"]


def _unique_product_slug(name: str) -> str:
    base = slugify(name) or "product"
    slug = base
    suffix = 1
    while Product.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


def _validate_row(row: dict) -> tuple[dict | None, list[str]]:
    errors = []

    name = (row.get("name") or "").strip()
    if not name:
        errors.append("name is required")

    category_name = (row.get("category") or "").strip()
    category = None
    if not category_name:
        errors.append("category is required")
    else:
        category = Category.objects.filter(name__iexact=category_name).first()
        if category is None:
            errors.append(f"category '{category_name}' does not exist")

    price = None
    price_raw = (row.get("price") or "").strip()
    if not price_raw:
        errors.append("price is required")
    else:
        try:
            price = Decimal(price_raw)
        except InvalidOperation:
            errors.append("price is not a valid number")
        else:
            if price <= 0:
                errors.append("price must be greater than 0")

    original_price = None
    original_price_raw = (row.get("original_price") or "").strip()
    if original_price_raw:
        try:
            original_price = Decimal(original_price_raw)
        except InvalidOperation:
            errors.append("original_price is not a valid number")

    stock_qty = 0
    stock_qty_raw = (row.get("stock_qty") or "").strip()
    if stock_qty_raw:
        try:
            stock_qty = int(stock_qty_raw)
        except ValueError:
            errors.append("stock_qty is not a valid integer")
        else:
            if stock_qty < 0:
                errors.append("stock_qty can't be negative")

    if errors:
        return None, errors

    return {
        "name": name,
        "category": category,
        "price": price,
        "original_price": original_price,
        "stock_qty": stock_qty,
        "description": (row.get("description") or "").strip(),
    }, []


def run_bulk_upload(job_id: int) -> None:
    job = BulkUploadJob.objects.select_related("seller").get(id=job_id)

    try:
        content = job.file.read().decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(content))
        rows = list(reader)
    except Exception as exc:
        job.status = BulkUploadJob.Status.FAILED
        job.error_report = [{"row": 0, "errors": [f"Could not read the file: {exc}"]}]
        job.save(update_fields=["status", "error_report"])
        return

    missing_columns = [c for c in REQUIRED_COLUMNS if reader.fieldnames and c not in reader.fieldnames]
    if reader.fieldnames is None or missing_columns:
        job.status = BulkUploadJob.Status.FAILED
        job.error_report = [{"row": 0, "errors": [f"Missing required column(s): {', '.join(missing_columns) or 'all'}"]}]
        job.save(update_fields=["status", "error_report"])
        return

    job.total_rows = len(rows)
    error_report = []
    success_count = 0

    for index, row in enumerate(rows, start=2):  # row 1 is the header
        data, errors = _validate_row(row)
        if errors:
            error_report.append({"row": index, "errors": errors})
            continue

        try:
            with transaction.atomic():
                Product.objects.create(
                    seller=job.seller,
                    category=data["category"],
                    name=data["name"],
                    slug=_unique_product_slug(data["name"]),
                    sku=f"SKU-{secrets.token_hex(4).upper()}",
                    price=data["price"],
                    original_price=data["original_price"],
                    stock_qty=data["stock_qty"],
                    description=data["description"],
                )
            success_count += 1
        except Exception as exc:
            error_report.append({"row": index, "errors": [f"Could not create product: {exc}"]})

    job.success_count = success_count
    job.error_count = len(error_report)
    job.error_report = error_report
    job.status = BulkUploadJob.Status.COMPLETED
    job.save(update_fields=["total_rows", "success_count", "error_count", "error_report", "status"])
