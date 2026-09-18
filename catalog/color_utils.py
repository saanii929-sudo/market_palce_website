COLOR_ALIASES = {
    "charcoal": "#36454f",
    "burgundy": "#800020",
    "wine": "#722f37",
    "maroon": "#800000",
    "mustard": "#ffdb58",
    "rose gold": "#b76e79",
    "rosegold": "#b76e79",
    "off white": "#f8f8f4",
    "offwhite": "#f8f8f4",
    "cream": "#fffdd0",
    "denim": "#1560bd",
    "camel": "#c19a6b",
    "mint": "#98ff98",
    "lilac": "#c8a2c8",
    "multicolor": "#ffffff",
    "multi": "#ffffff",
    "assorted": "#ffffff",
}


def resolve_css_color(value: str | None) -> str | None:
    if not value:
        return None
    key = value.strip().lower()
    if key in COLOR_ALIASES:
        return COLOR_ALIASES[key]
    return key.replace(" ", "").replace("-", "")
