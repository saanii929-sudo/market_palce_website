from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Category(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    icon = models.ImageField(upload_to="categories/", null=True, blank=True)
    icon_url = models.URLField(blank=True, max_length=500)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("10.00"),
        help_text="Platform commission percentage on sales in this category (e.g. 10.00 for 10%).",
    )

    class Meta:
        verbose_name_plural = "categories"
        ordering = ["display_order", "name"]

    def __str__(self):
        return self.name

    @property
    def resolved_icon_url(self) -> str | None:
        if self.icon_url:
            return self.icon_url
        if self.icon:
            return self.icon.url
        return None


class Subcategory(TimeStampedModel):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="subcategories")
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "subcategories"
        ordering = ["display_order", "name"]
        unique_together = ("category", "name")

    def __str__(self):
        return f"{self.category.name} / {self.name}"


class Brand(TimeStampedModel):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True)
    logo = models.ImageField(upload_to="brands/", null=True, blank=True)
    logo_url = models.URLField(blank=True, max_length=500)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def resolved_logo_url(self) -> str | None:
        return self.logo_url or (self.logo.url if self.logo else None)


class Seller(TimeStampedModel):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="seller_profile"
    )
    business_name = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True)
    tagline = models.CharField(max_length=255, blank=True)
    logo = models.ImageField(upload_to="sellers/", null=True, blank=True)
    logo_url = models.URLField(blank=True, max_length=500)
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    is_verified = models.BooleanField(default=False)
    is_featured = models.BooleanField(default=False)

    primary_category = models.ForeignKey(
        "catalog.Category", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    support_phone = models.CharField(max_length=20, blank=True)

    class Meta:
        ordering = ["business_name"]

    def __str__(self):
        return self.business_name

    @property
    def resolved_logo_url(self) -> str | None:
        return self.logo_url or (self.logo.url if self.logo else None)


class Product(TimeStampedModel):
    seller = models.ForeignKey(Seller, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="products")
    subcategory = models.ForeignKey(
        Subcategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=280, unique=True)
    description = models.TextField(blank=True)

    price = models.DecimalField(max_digits=10, decimal_places=2)
    original_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    cost_price = models.DecimalField(
        max_digits=10, decimal_places=2, default=Decimal("0.00"),
        help_text="Weighted-average unit cost, updated when purchase orders are received. Used for COGS/profit reporting.",
    )
    sku = models.CharField(max_length=64, unique=True)
    barcode = models.CharField(
        max_length=64, unique=True, null=True, blank=True,
        help_text="Scanned at the point of sale; falls back to SKU when blank.",
    )
    stock_qty = models.PositiveIntegerField(default=0)

    is_returnable = models.BooleanField(
        default=True, help_text="Whether a customer can request a return for this product after delivery."
    )
    return_window_days = models.PositiveIntegerField(
        default=7, help_text="Days after delivery a return can still be requested."
    )

    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)

    avg_rating = models.DecimalField(max_digits=3, decimal_places=2, default=Decimal("0.00"))
    review_count = models.PositiveIntegerField(default=0)
    view_count = models.PositiveIntegerField(default=0)
    sold_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_active", "is_featured"]),
            models.Index(fields=["-sold_count"]),
        ]

    def __str__(self):
        return self.name

    @property
    def discount_percent(self) -> int | None:
        if not self.original_price or self.original_price <= self.price:
            return None
        return round((self.original_price - self.price) / self.original_price * 100)

    @property
    def in_stock(self) -> bool:
        return self.stock_qty > 0


class ProductImage(TimeStampedModel):
    """`image` holds an uploaded file; `external_url` lets seed/demo data (or
    a seller pasting a hosted photo link) reference an image without our
    storage backend - resolved_url prefers whichever is set."""

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/", blank=True)
    external_url = models.URLField(blank=True, max_length=500)
    display_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]

    @property
    def resolved_url(self) -> str | None:
        if self.external_url:
            return self.external_url
        if self.image:
            return self.image.url
        return None


class ProductVariant(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    size = models.CharField(max_length=30, blank=True)
    color = models.CharField(max_length=30, blank=True)
    sku = models.CharField(max_length=64, unique=True, null=True, blank=True)
    stock_qty = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ("product", "size", "color")

    def __str__(self):
        bits = [b for b in [self.size, self.color] if b]
        return f"{self.product.name} ({', '.join(bits)})" if bits else self.product.name

    @property
    def in_stock(self) -> bool:
        return self.stock_qty > 0


class FlashDeal(TimeStampedModel):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="flash_deals")
    deal_price = models.DecimalField(max_digits=10, decimal_places=2)
    stock_qty = models.PositiveIntegerField(default=0)
    stock_sold = models.PositiveIntegerField(default=0)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ends_at"]
        indexes = [models.Index(fields=["is_active", "starts_at", "ends_at"])]

    def __str__(self):
        return f"Flash deal: {self.product.name}"

    @property
    def percent_stock_sold(self):
        denominator = self.stock_sold + self.stock_qty
        if denominator == 0:
            return 0
        return round((self.stock_sold / denominator) * 100)

    @property
    def is_within_window(self):
        now = timezone.now()
        return self.starts_at <= now <= self.ends_at

    @property
    def is_live(self):
        return self.is_active and self.is_within_window and self.stock_qty > 0


class Collection(TimeStampedModel):
    title = models.CharField(max_length=150)
    slug = models.SlugField(max_length=170, unique=True)
    banner_image = models.ImageField(upload_to="collections/", null=True, blank=True)
    banner_image_url = models.URLField(blank=True, max_length=500)
    linked_category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="collections"
    )
    products = models.ManyToManyField(Product, related_name="collections", blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order", "title"]

    def __str__(self):
        return self.title

    @property
    def resolved_banner_url(self) -> str | None:
        if self.banner_image_url:
            return self.banner_image_url
        if self.banner_image:
            return self.banner_image.url
        return None


class Banner(TimeStampedModel):
    title = models.CharField(max_length=150)
    subtitle = models.CharField(max_length=255, blank=True)
    image = models.ImageField(upload_to="banners/", blank=True)
    image_url = models.URLField(blank=True, max_length=500)
    cta_label = models.CharField(max_length=60, blank=True)
    cta_link = models.CharField(max_length=255, blank=True)
    active_from = models.DateTimeField(null=True, blank=True)
    active_to = models.DateTimeField(null=True, blank=True)
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["display_order"]

    def __str__(self):
        return self.title

    @property
    def resolved_image_url(self) -> str | None:
        if self.image_url:
            return self.image_url
        if self.image:
            return self.image.url
        return None

    @property
    def is_currently_active(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.active_from and now < self.active_from:
            return False
        if self.active_to and now > self.active_to:
            return False
        return True


