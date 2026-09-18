from django import template

from catalog.color_utils import resolve_css_color

register = template.Library()

_PALETTE = [
    ("#1f2937", "#4b5563"),  # slate
    ("#7c2d12", "#c2410c"),  # rust
    ("#14532d", "#15803d"),  # forest
    ("#1e3a8a", "#2563eb"),  # blue
    ("#701a75", "#a21caf"),  # plum
    ("#78350f", "#b45309"),  # amber
    ("#164e63", "#0891b2"),  # teal
]


@register.filter
def placeholder_gradient(obj) -> str:
    """Deterministic two-tone gradient for products/categories with no real
    photo yet - same id always maps to the same look."""
    key = getattr(obj, "id", 0) or 0
    dark, light = _PALETTE[key % len(_PALETTE)]
    return f"linear-gradient(135deg, {dark}, {light})"


@register.filter
def initial(value) -> str:
    value = (value or "").strip()
    return value[0].upper() if value else "?"


@register.filter
def percent_of(value, total):
    try:
        value, total = float(value), float(total)
    except (TypeError, ValueError):
        return 0
    if total <= 0:
        return 0
    return round(value / total * 100)


@register.filter
def stars(value):
    """Range object of length round(value), for {% for _ in rating|stars %}."""
    try:
        return range(round(float(value)))
    except (TypeError, ValueError):
        return range(0)


_ORDER_DISPLAY_STATUS = {
    "processing": "Processing",
    "shipped": "In Transit",
    "out_for_delivery": "In Transit",
    "delivered": "Delivered",
    "cancelled": "Cancelled",
}

_ORDER_STATUS_BADGE = {
    "processing": "bg-gray-100 text-gray-600",
    "shipped": "bg-amber-100 text-amber-700",
    "out_for_delivery": "bg-amber-100 text-amber-700",
    "delivered": "bg-green-100 text-green-700",
    "cancelled": "bg-red-100 text-red-600",
}


@register.filter
def order_display_status(status: str) -> str:
    return _ORDER_DISPLAY_STATUS.get(status, status)


@register.filter
def order_status_badge(status: str) -> str:
    return _ORDER_STATUS_BADGE.get(status, "bg-gray-100 text-gray-600")


@register.filter
def css_color(value):
    """Best-effort CSS color for a free-text color name, for rendering a
    swatch - returns None (render no swatch, just the text label) only when
    the value is empty. See catalog.color_utils for the alias table."""
    return resolve_css_color(value)
