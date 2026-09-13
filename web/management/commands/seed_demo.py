import datetime
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from accounts.models import Address, UserInterest
from cart.models import Coupon
from catalog.models import (
    Banner,
    Brand,
    Category,
    Collection,
    FlashDeal,
    Product,
    ProductImage,
    ProductVariant,
    Seller,
    Subcategory,
)
from cms.models import StaticPage
from notifications.models import Notification
from orders.models import DeliveryMethod, Order, OrderItem, Payment, PaymentMethod, ReturnRequest, Shipment
from orders.services.returns import ReturnError, request_return
from payments.models import PaymentMethodToken
from pos.models import (
    Customer,
    Discount,
    Employee,
    Expense,
    POSSale,
    POSSaleItem,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from pos.services import create_invoice, mark_payroll_paid, receive_purchase_order, record_invoice_payment, run_payroll
from reviews.models import Review
from sellers.models import Payout
from support.models import FAQ, SupportTicket
from wishlist.models import WishlistItem

User = get_user_model()


def _img(photo_id: str) -> str:
    return f"https://images.unsplash.com/{photo_id}?auto=format&fit=crop&w=900&q=80"


FOOTBALL_BOOTS = [_img(p) for p in [
    "photo-1570498839593-e565b39455fc", "photo-1579952363873-27f3bade9f55",
    "photo-1511886929837-354d827aae26", "photo-1529900748604-07564a03e7a6",
]]
JERSEYS = [_img(p) for p in [
    "photo-1577212017184-80cc0da11082", "photo-1616124619460-ff4ed8f4683c",
    "photo-1552066379-e7bfd22155c5", "photo-1662096909714-e2f206d0a636",
]]
BASKETBALL = [_img(p) for p in [
    "photo-1546519638-68e109498ffc", "photo-1608245449230-4ac19066d2d0",
    "photo-1627627256672-027a4613d028", "photo-1519861531473-9200262188bf",
]]
RUNNING = [_img(p) for p in [
    "photo-1542291026-7eec264c27ff", "photo-1606107557195-0e29a4b5b4aa",
    "photo-1571008887538-b36bb32f4571", "photo-1560769629-975ec94e6a86",
]]
GYM = [_img(p) for p in [
    "photo-1534438327276-14e5300c3a48", "photo-1576678927484-cc907957088c",
    "photo-1583454110551-21f2fa2afe61", "photo-1544033527-b192daee1f5b",
]]
TENNIS = [_img(p) for p in [
    "photo-1554068865-24cecd4e34b8", "photo-1545151414-8a948e1ea54f", "photo-1620742820748-87c09249a72a",
]]
BOXING = [_img(p) for p in [
    "photo-1549719386-74dfcbf7dbed", "photo-1552072092-7f9b8d63efcb", "photo-1583473848882-f9a5bc7fd2ee",
]]
CYCLING = [_img(p) for p in [
    "photo-1541625602330-2277a4c46182", "photo-1534787238916-9ba6764efd4f", "photo-1444491741275-3747c53c99b4",
]]
SWIMMING = [_img(p) for p in [
    "photo-1562016600-ece13e8ba570", "photo-1568145675395-66a2eda0c6d7", "photo-1591285713698-598d587de63e",
]]
ACCESSORIES = [_img(p) for p in [
    "photo-1586022045076-aee0a185180b", "photo-1622260615656-96d7c7ad6c4c", "photo-1505308144658-03c69861061a",
]]
HERO_IMAGE = _img("photo-1517649763962-0c623066013b")


# (category slug, category name, subcategory names, brand slug, brand name,
#  seller slug, seller name, [(slug, name, price, original_price, sold_count,
#  is_featured, image_pool), ...])
CATALOG_SPEC = [
    ("football", "Football", ["Boots", "Jerseys", "Balls", "Goalkeeper gear"], "vantage", "Vantage", "sportmart", "SportMart", [
        ("pro-match-boots", "Pro Match Football Boots", "329.00", "449.00", 212, True, FOOTBALL_BOOTS),
        ("club-jersey-2026", "Club Jersey 2026 Home Kit", "210.00", "260.00", 410, False, JERSEYS),
        ("goalkeeper-gloves-pro", "Goalkeeper Gloves Pro", "150.00", "190.00", 88, False, FOOTBALL_BOOTS[1:]),
    ]),
    ("basketball", "Basketball", ["Balls", "Jerseys", "Footwear"], "northmark", "Northmark", "northmark-store", "Northmark Sports Store", [
        ("court-grip-basketball", "Court Grip Basketball", "95.00", "120.00", 164, False, BASKETBALL),
        ("pro-basketball-jersey", "Pro Basketball Jersey", "140.00", "175.00", 97, False, JERSEYS[1:]),
    ]),
    ("running", "Running", ["Shoes", "Apparel"], "ridgeline", "Ridgeline", "sportmart", "SportMart", [
        ("trailhead-running-shoes", "Trailhead Running Shoes", "412.00", "520.00", 301, True, RUNNING),
        ("lightweight-running-shorts", "Lightweight Running Shorts", "89.00", "110.00", 76, False, RUNNING[2:]),
    ]),
    ("gym-fitness", "Gym & Fitness", ["Weights", "Accessories"], "solstice", "Solstice", "sportmart", "SportMart", [
        ("adjustable-dumbbell-20kg", "Adjustable Dumbbell Set 20kg", "640.00", "760.00", 89, False, GYM),
        ("resistance-bands-set", "Resistance Bands Set", "65.00", "85.00", 54, False, GYM[2:]),
    ]),
    ("tennis", "Tennis", ["Rackets", "Apparel"], "baseline", "Baseline", "sportmart", "SportMart", [
        ("carbon-tennis-racket", "Carbon Tennis Racket", "540.00", "650.00", 43, False, TENNIS),
    ]),
    ("boxing", "Boxing", ["Gloves", "Bags"], "northmark", "Northmark", "northmark-store", "Northmark Sports Store", [
        ("pro-boxing-gloves-12oz", "Pro Boxing Gloves 12oz", "185.00", "230.00", 145, False, BOXING),
    ]),
    ("cycling", "Cycling", ["Helmets", "Bikes"], "pulsewear", "Pulsewear", "sportmart", "SportMart", [
        ("road-bike-helmet", "Road Bike Helmet", "220.00", "280.00", 38, False, CYCLING),
    ]),
    ("swimming", "Swimming", ["Goggles", "Swimwear"], "kinetic", "Kinetic", "sportmart", "SportMart", [
        ("racing-swim-goggles", "Racing Swim Goggles", "75.00", "95.00", 61, False, SWIMMING),
    ]),
    ("sportswear", "Sportswear", ["Hoodies", "Tracksuits"], "kinetic", "Kinetic", "sportmart", "SportMart", [
        ("flex-training-hoodie", "Flex Training Hoodie", "189.00", "230.00", 72, False, JERSEYS[2:]),
    ]),
    ("accessories", "Accessories", ["Bags", "Bottles"], "pulsewear", "Pulsewear", "sportmart", "SportMart", [
        ("pro-sports-duffel-bag", "Pro Sports Duffel Bag", "210.00", "260.00", 29, False, ACCESSORIES),
    ]),
]


class Command(BaseCommand):
    help = "Seeds every model with realistic demo data (categories, products with real photos, brands, sellers, flash deals, collections, banners, reviews, orders, addresses, payment methods, notifications, FAQs, static pages) so the site never looks empty."

    def handle(self, *args, **options):
        self.stdout.write("Seeding categories, brands, sellers, products...")
        products_by_slug = self._seed_catalog()

        self.stdout.write("Seeding collections, banners...")
        self._seed_collections_and_banners(products_by_slug)

        self.stdout.write("Seeding seller payouts...")
        self._seed_payouts()

        self.stdout.write("Seeding POS employees and in-store sales...")
        self._seed_pos(products_by_slug)

        self.stdout.write("Seeding suppliers, purchase orders, discounts, customers, payroll...")
        self._seed_store_ops(products_by_slug)

        self.stdout.write("Seeding delivery/payment methods, FAQs, static pages...")
        self._seed_lookups()
        self._seed_faqs()
        self._seed_static_pages()

        self.stdout.write("Seeding demo user (address, payment method, wishlist, order, reviews, notifications)...")
        self._seed_demo_user(products_by_slug)

        self.stdout.write("Seeding platform superadmin account...")
        self._seed_superadmin()

        self.stdout.write(self.style.SUCCESS("Demo data seeded."))

    def _seed_superadmin(self):
        admin_user, _ = User.objects.get_or_create(
            email="superadmin@example.com",
            defaults={"is_email_verified": True, "full_name": "Platform Admin", "role": User.Role.ADMIN},
        )
        admin_user.set_password("StrongPass123!")
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.is_email_verified = True
        admin_user.role = User.Role.ADMIN
        admin_user.save(update_fields=["password", "is_staff", "is_superuser", "is_email_verified", "role"])

    # -- catalog ------------------------------------------------------------

    def _seed_catalog(self):
        brand_cache, seller_cache, category_cache = {}, {}, {}
        products_by_slug = {}

        for cat_slug, cat_name, sub_names, brand_slug, brand_name, seller_slug, seller_name, product_specs in CATALOG_SPEC:
            category = category_cache.get(cat_slug)
            if category is None:
                category, _ = Category.objects.get_or_create(slug=cat_slug, defaults={"name": cat_name})
                category_cache[cat_slug] = category
                for i, sub_name in enumerate(sub_names):
                    Subcategory.objects.get_or_create(
                        category=category, name=sub_name,
                        defaults={"slug": f"{cat_slug}-{sub_name.lower().replace(' ', '-')}", "display_order": i},
                    )

            brand = brand_cache.get(brand_slug)
            if brand is None:
                brand, _ = Brand.objects.get_or_create(slug=brand_slug, defaults={"name": brand_name})
                brand_cache[brand_slug] = brand

            seller = seller_cache.get(seller_slug)
            if seller is None:
                seller, _ = Seller.objects.get_or_create(
                    slug=seller_slug,
                    defaults=dict(
                        business_name=seller_name,
                        tagline="Official sports gear reseller. Free returns, same-day dispatch on in-stock items.",
                        rating="4.8", is_verified=True, is_featured=True,
                        logo_url=BASKETBALL[0] if seller_slug == "northmark-store" else "",
                    ),
                )
                seller_cache[seller_slug] = seller

            if seller_slug == "northmark-store":
                seller_user, _ = User.objects.get_or_create(
                    email="seller@example.com",
                    defaults={"is_email_verified": True, "full_name": "Northmark Sports", "role": User.Role.SELLER},
                )
                # Always (re)set the known demo password/role, every run - this account
                # may have been created earlier (e.g. by an ad-hoc test) with a different
                # password, and get_or_create alone would never correct that.
                seller_user.set_password("StrongPass123!")
                seller_user.is_email_verified = True
                seller_user.role = User.Role.SELLER
                seller_user.save(update_fields=["password", "is_email_verified", "role"])
                if seller.user_id != seller_user.id or not seller.support_phone:
                    seller.user = seller_user
                    seller.primary_category = category
                    seller.support_phone = "024 700 1122"
                    seller.save(update_fields=["user", "primary_category", "support_phone"])

            first_subcategory = Subcategory.objects.filter(category=category).first()

            for slug, name, price, original_price, sold_count, is_featured, image_pool in product_specs:
                product, created = Product.objects.get_or_create(
                    slug=slug,
                    defaults=dict(
                        name=name, category=category, subcategory=first_subcategory, brand=brand, seller=seller,
                        price=price, original_price=original_price, sku=f"SKU-{slug.upper()}", stock_qty=40,
                        sold_count=sold_count, is_featured=is_featured, avg_rating="0.00", review_count=0,
                        description=(
                            f"Engineered for grip and control, the {name} is built to hold up through a full "
                            "season of play without losing shape or performance. A trusted pick for athletes who "
                            "need gear that keeps pace with training and match day alike."
                        ),
                    ),
                )
                if created:
                    for i, url in enumerate(image_pool):
                        ProductImage.objects.create(product=product, external_url=url, display_order=i)
                    for size in ["S", "M", "L", "XL"]:
                        ProductVariant.objects.get_or_create(product=product, size=size, color="", defaults={"stock_qty": 12})
                products_by_slug[slug] = product

        return products_by_slug

    def _seed_collections_and_banners(self, products_by_slug):
        Banner.objects.get_or_create(
            title="Fresh kits. New colours.",
            defaults=dict(
                subtitle="The latest club and training jerseys just landed.",
                image_url=HERO_IMAGE, cta_label="Shop now", cta_link="/deals/",
            ),
        )

        collection_specs = [
            ("football-collection", "Football", "football", FOOTBALL_BOOTS[0]),
            ("gym-collection", "Gym", "gym-fitness", GYM[0]),
            ("running-collection", "Running", "running", RUNNING[0]),
            ("basketball-collection", "Basketball", "basketball", BASKETBALL[0]),
        ]
        for i, (slug, title, cat_slug, image) in enumerate(collection_specs):
            category = Category.objects.filter(slug=cat_slug).first()
            collection, _ = Collection.objects.get_or_create(
                slug=slug, defaults=dict(title=title, linked_category=category, banner_image_url=image, display_order=i)
            )
            matching = [p for p in products_by_slug.values() if p.category_id == (category.id if category else None)]
            if matching:
                collection.products.add(*matching)

        if products_by_slug.get("club-jersey-2026"):
            FlashDeal.objects.get_or_create(
                product=products_by_slug["club-jersey-2026"],
                defaults=dict(
                    deal_price="210.00", stock_qty=38, stock_sold=62,
                    starts_at=timezone.now() - datetime.timedelta(hours=1),
                    ends_at=timezone.now() + datetime.timedelta(hours=3),
                ),
            )
        if products_by_slug.get("court-grip-basketball"):
            FlashDeal.objects.get_or_create(
                product=products_by_slug["court-grip-basketball"],
                defaults=dict(
                    deal_price="95.00", stock_qty=20, stock_sold=15,
                    starts_at=timezone.now() - datetime.timedelta(hours=2),
                    ends_at=timezone.now() + datetime.timedelta(hours=5),
                ),
            )

    def _seed_payouts(self):
        seller = Seller.objects.filter(slug="northmark-store").first()
        if seller is None:
            return
        today = timezone.now().date()
        payout_specs = [
            (today - datetime.timedelta(days=44), "4560.00", Payout.Status.PAID),
            (today - datetime.timedelta(days=29), "3890.00", Payout.Status.PAID),
            (today - datetime.timedelta(days=13), "4320.00", Payout.Status.PAID),
            (today + datetime.timedelta(days=2), "5120.00", Payout.Status.SCHEDULED),
        ]
        for payout_date, amount, status in payout_specs:
            Payout.objects.get_or_create(
                seller=seller, payout_date=payout_date,
                defaults={"amount": amount, "method": "MTN MoMo", "status": status},
            )

    def _seed_pos(self, products_by_slug):
        seller = Seller.objects.filter(slug="northmark-store").first()
        if seller is None:
            return

        employee_specs = [
            ("Ama Serwaa", Employee.Role.MANAGER, "1234"),
            ("Kwesi Boateng", Employee.Role.CASHIER, "4321"),
        ]
        employees = []
        for full_name, role, pin in employee_specs:
            employee, created = Employee.objects.get_or_create(
                seller=seller, full_name=full_name, defaults={"role": role}
            )
            if created:
                employee.set_pin(pin)
                employee.save(update_fields=["pin_hash"])
            employees.append(employee)

        if POSSale.objects.filter(seller=seller).exists():
            return

        now = timezone.now()
        sale_specs = [
            (0, "court-grip-basketball", 2, POSSale.PaymentMethod.CASH, employees[1]),
            (0, "pro-boxing-gloves-12oz", 1, POSSale.PaymentMethod.MOBILE_MONEY, employees[0]),
            (2, "pro-basketball-jersey", 1, POSSale.PaymentMethod.CARD, employees[1]),
            (6, "court-grip-basketball", 1, POSSale.PaymentMethod.CASH, employees[1]),
            (15, "pro-boxing-gloves-12oz", 2, POSSale.PaymentMethod.CASH, employees[0]),
            (40, "pro-basketball-jersey", 3, POSSale.PaymentMethod.MOBILE_MONEY, employees[1]),
            (100, "court-grip-basketball", 1, POSSale.PaymentMethod.CARD, employees[0]),
        ]
        for days_ago, slug, qty, payment_method, employee in sale_specs:
            product = products_by_slug.get(slug)
            if not product:
                continue
            sold_at = now - datetime.timedelta(days=days_ago)
            subtotal = product.price * qty
            sale = POSSale.objects.create(
                seller=seller, employee=employee, payment_method=payment_method,
                subtotal=subtotal, total=subtotal,
                amount_tendered=subtotal if payment_method == POSSale.PaymentMethod.CASH else None,
                sold_at=sold_at,
            )
            POSSaleItem.objects.create(sale=sale, product=product, qty=qty, unit_price=product.price)
            product.stock_qty = max(0, product.stock_qty - qty)
            product.sold_count = product.sold_count + qty
            product.save(update_fields=["stock_qty", "sold_count"])

    def _seed_store_ops(self, products_by_slug):
        seller = Seller.objects.filter(slug="northmark-store").first()
        if seller is None:
            return

        supplier, _ = Supplier.objects.get_or_create(
            seller=seller, name="Accra Sportswear Distributors",
            defaults={"contact_name": "Yaw Osei", "phone": "+233244000111", "email": "sales@accrasportswear.example"},
        )

        if not PurchaseOrder.objects.filter(seller=seller).exists():
            basketball = products_by_slug.get("court-grip-basketball")
            jersey = products_by_slug.get("pro-basketball-jersey")
            if basketball and jersey:
                today = timezone.now().date()
                po = PurchaseOrder.objects.create(seller=seller, supplier=supplier, notes="Restock for the month")
                PurchaseOrderItem.objects.create(
                    purchase_order=po, product=basketball, qty=20, unit_cost=Decimal("55.00"),
                    expiry_date=today - datetime.timedelta(days=5),
                )
                PurchaseOrderItem.objects.create(
                    purchase_order=po, product=jersey, qty=15, unit_cost=Decimal("90.00"),
                    expiry_date=today + datetime.timedelta(days=10),
                )
                receive_purchase_order(po)

        Discount.objects.get_or_create(
            seller=seller, name="Loyalty 10%",
            defaults={"discount_type": Discount.Type.PERCENTAGE, "value": Decimal("10.00")},
        )
        Discount.objects.get_or_create(
            seller=seller, name="GH₵20 off",
            defaults={"discount_type": Discount.Type.FIXED, "value": Decimal("20.00")},
        )

        Customer.objects.get_or_create(
            seller=seller, full_name="Kojo Asante", defaults={"phone": "+233209990001"}
        )
        Customer.objects.get_or_create(
            seller=seller, full_name="Efua Mensima", defaults={"phone": "+233209990002"}
        )

        if not seller.pay_runs.exists():
            today = timezone.now().date()
            period_start = today.replace(day=1) - datetime.timedelta(days=30)
            period_end = period_start + datetime.timedelta(days=29)
            pay_run = run_payroll(seller, period_start, period_end)
            mark_payroll_paid(pay_run)

        if not seller.expenses.filter(category="rent").exists():
            Expense.objects.create(
                seller=seller, category=Expense.Category.RENT,
                description="Shop rent", amount=Decimal("1200.00"),
                incurred_on=timezone.now().date().replace(day=1),
            )

        if not seller.invoices.exists():
            today = timezone.now().date()
            kojo = Customer.objects.filter(seller=seller, full_name="Kojo Asante").first()
            efua = Customer.objects.filter(seller=seller, full_name="Efua Mensima").first()

            paid_invoice = create_invoice(
                seller=seller, customer=kojo, customer_name=kojo.full_name if kojo else "Kojo Asante",
                issue_date=today - datetime.timedelta(days=20), due_date=today - datetime.timedelta(days=6),
                tax_rate=Decimal("0.00"),
                line_items=[{"description": "Team basketball kit order", "qty": "5", "unit_price": "95.00"}],
            )
            record_invoice_payment(paid_invoice, amount=paid_invoice.total, method="mobile_money",
                                    paid_on=today - datetime.timedelta(days=18))

            overdue_invoice = create_invoice(
                seller=seller, customer=efua, customer_name=efua.full_name if efua else "Efua Mensima",
                issue_date=today - datetime.timedelta(days=25), due_date=today - datetime.timedelta(days=10),
                tax_rate=Decimal("0.00"),
                line_items=[{"description": "Boxing gloves - bulk order", "qty": "8", "unit_price": "185.00"}],
            )
            record_invoice_payment(overdue_invoice, amount=Decimal("500.00"), method="cash",
                                    paid_on=today - datetime.timedelta(days=20))

            create_invoice(
                seller=seller, customer=None, customer_name="Accra Sports Academy",
                customer_phone="+233247001234", issue_date=today, due_date=today + datetime.timedelta(days=14),
                tax_rate=Decimal("5.00"),
                line_items=[{"description": "Jerseys for school team (20 units)", "qty": "20", "unit_price": "90.00"}],
                notes="Thank you for choosing Northmark Sports.",
                terms="Payment due within 14 days of receipt.",
            )

    def _seed_lookups(self):
        DeliveryMethod.objects.get_or_create(
            code="standard", defaults=dict(name="Standard", price="5.00", eta_days_min=2, eta_days_max=5)
        )
        DeliveryMethod.objects.get_or_create(
            code="express", defaults=dict(name="Express", price="15.00", eta_days_min=1, eta_days_max=2)
        )
        PaymentMethod.objects.get_or_create(code="cash_on_delivery", defaults={"name": "Cash on Delivery"})
        PaymentMethod.objects.get_or_create(code="card", defaults={"name": "Card"})
        PaymentMethod.objects.get_or_create(code="mobile_money", defaults={"name": "Mobile Money"})
        Coupon.objects.get_or_create(
            code="WELCOME10",
            defaults=dict(discount_type=Coupon.DiscountType.PERCENTAGE, value="10.00",
                          min_order_amount="50.00", max_discount_amount="100.00"),
        )
        Coupon.objects.get_or_create(
            code="SAVE20",
            defaults=dict(discount_type=Coupon.DiscountType.FIXED, value="20.00", min_order_amount="150.00"),
        )

    def _seed_faqs(self):
        faq_specs = [
            ("How do I track my order?", "Open My Orders and tap an order to see its live tracking timeline.", FAQ.Topic.ORDERS),
            ("Can I change my delivery address after ordering?", "Contact support right away - once an order ships, the address can't be changed.", FAQ.Topic.ORDERS),
            ("What payment methods are supported?", "Card, Mobile Money, and Cash on Delivery.", FAQ.Topic.PAYMENTS),
            ("Is it safe to save my card?", "Yes - we never store your card number, only a token from the payment gateway.", FAQ.Topic.PAYMENTS),
            ("How do returns work?", "Contact support within 14 days of delivery to start a return.", FAQ.Topic.RETURNS),
            ("How do I update my profile?", "Go to Overview in your account and edit your details there.", FAQ.Topic.ACCOUNT),
            ("How do I become a seller?", "Apply from the Become a Seller page - approval usually takes 1-2 business days.", FAQ.Topic.SELLING),
        ]
        for question, answer, topic in faq_specs:
            FAQ.objects.get_or_create(question=question, defaults={"answer": answer, "topic": topic})

    def _seed_static_pages(self):
        pages = [
            (StaticPage.Slug.ABOUT, "About SportTech", "<p>We connect verified sellers with athletes across Ghana.</p>"),
            (StaticPage.Slug.SHIPPING, "Shipping", "<p>Standard delivery takes 2-5 days; Express takes 1-2 days.</p>"),
            (StaticPage.Slug.RETURNS, "Returns", "<p>Returns are accepted within 14 days of delivery.</p>"),
            (StaticPage.Slug.PRIVACY, "Privacy Policy", "<p>We only use your data to fulfil your orders.</p>"),
            (StaticPage.Slug.TERMS, "Terms of Service", "<p>By using SportTech you agree to these terms.</p>"),
        ]
        for slug, title, body in pages:
            StaticPage.objects.get_or_create(slug=slug, defaults={"title": title, "body": body})

    # -- demo user + orders + reviews ---------------------------------------

    def _seed_demo_user(self, products_by_slug):
        user, created = User.objects.get_or_create(
            email="demo@example.com", defaults={"is_email_verified": True, "full_name": "Jordan Mensah"}
        )
        if created:
            user.set_password("StrongPass123!")
            user.save(update_fields=["password"])

        Address.objects.get_or_create(
            user=user, label="Home",
            defaults=dict(recipient_name="Jordan Mensah", phone="+233201234567", city="Accra",
                          line1="12 Liberation Ave", line2="Airport Residential", region="Greater Accra",
                          country="Ghana", is_default=True),
        )
        Address.objects.get_or_create(
            user=user, label="Work",
            defaults=dict(recipient_name="Jordan Mensah", phone="+233201234567", city="Accra",
                          line1="Ridge Towers, 3rd Floor", line2="Ridge", region="Greater Accra", country="Ghana"),
        )

        PaymentMethodToken.objects.get_or_create(
            user=user, last4="4821",
            defaults=dict(gateway="paystack", token="tok_demo_visa_4821", brand="visa",
                          expiry_month=9, expiry_year=2028, is_default=True),
        )
        PaymentMethodToken.objects.get_or_create(
            user=user, last4="0532",
            defaults=dict(gateway="paystack", token="tok_demo_momo_0532", brand="mtn_momo"),
        )

        for slug in ["pro-match-boots", "carbon-tennis-racket", "flex-training-hoodie"]:
            product = products_by_slug.get(slug)
            if product:
                WishlistItem.objects.get_or_create(user=user, product=product)

        for cat_slug in ["football", "gym-fitness"]:
            category = Category.objects.filter(slug=cat_slug).first()
            if category:
                UserInterest.objects.get_or_create(user=user, category=category)

        SupportTicket.objects.get_or_create(
            user=user, subject="Where is my order?",
            defaults=dict(channel=SupportTicket.Channel.CHAT,
                          message="Hi, my order has been processing for a few days - any update on shipping?",
                          status=SupportTicket.Status.OPEN),
        )

        self._seed_orders_and_reviews(user, products_by_slug)
        self._seed_return_request(user)
        self._seed_notifications(user)

    def _seed_orders_and_reviews(self, user, products_by_slug):
        delivery = DeliveryMethod.objects.get(code="standard")
        payment_method = PaymentMethod.objects.get(code="card")
        address = user.addresses.filter(is_default=True).first()
        if Order.objects.filter(user=user).exists() or address is None:
            return

        order_specs = [
            ("pro-match-boots", 1, Order.Status.DELIVERED, 28),
            ("trailhead-running-shoes", 1, Order.Status.SHIPPED, 14),
            ("adjustable-dumbbell-20kg", 1, Order.Status.PROCESSING, 2),
            ("pro-boxing-gloves-12oz", 1, Order.Status.CANCELLED, 60),
        ]
        for product_slug, qty, target_status, days_ago in order_specs:
            product = products_by_slug.get(product_slug)
            if not product:
                continue

            order = Order(
                user=user, subtotal=product.price, delivery_fee=delivery.price,
                total=str(float(product.price) + float(delivery.price)),
                delivery_method=delivery, payment_method=payment_method,
                placed_at=timezone.now() - datetime.timedelta(days=days_ago),
            )
            order.snapshot_address(address)
            order.save()
            Order.objects.filter(id=order.id).update(placed_at=timezone.now() - datetime.timedelta(days=days_ago))
            item = OrderItem.objects.create(order=order, product=product, qty=qty, unit_price=product.price)

            payment_status = Payment.Status.FAILED if target_status == Order.Status.CANCELLED else Payment.Status.SUCCESS
            Payment.objects.create(
                order=order, gateway=Payment.Gateway.PAYSTACK, status=payment_status, amount=order.total,
                gateway_reference=f"PAY-DEMO-{order.id}",
            )

            if target_status == Order.Status.SHIPPED:
                order.transition_to(Order.Status.SHIPPED, note="Left the warehouse.")
                Shipment.objects.get_or_create(
                    order=order, defaults=dict(courier_name="Speedaf", tracking_number=f"SPD{order.id:08d}",
                                                current_status="In transit"),
                )
            elif target_status == Order.Status.CANCELLED:
                order.transition_to(Order.Status.CANCELLED, note="Cancelled by customer.")
            elif target_status == Order.Status.DELIVERED:
                order.transition_to(Order.Status.SHIPPED, note="Left the warehouse.")
                order.transition_to(Order.Status.OUT_FOR_DELIVERY, note="Out with courier.")
                order.transition_to(Order.Status.DELIVERED, note="Delivered.")
                Shipment.objects.get_or_create(
                    order=order, defaults=dict(courier_name="Speedaf", tracking_number=f"SPD{order.id:08d}",
                                                current_status="Delivered"),
                )
                Review.objects.get_or_create(
                    order_item=item,
                    defaults=dict(user=user, product=product, rating=5, comment="Excellent quality, fits true to size."),
                )

        # A handful of other reviewers on the flagship product, for a
        # realistic-looking rating breakdown.
        reviewer_specs = [("reviewer1@example.com", 5, "Great boots, lasted the whole season."),
                           ("reviewer2@example.com", 4, "Good grip but runs a little narrow."),
                           ("reviewer3@example.com", 5, "Best purchase this year.")]
        boots = products_by_slug.get("pro-match-boots")
        if boots:
            for email, rating, comment in reviewer_specs:
                reviewer, _ = User.objects.get_or_create(email=email, defaults={"is_email_verified": True, "full_name": email.split("@")[0].title()})
                order = Order(
                    user=reviewer, subtotal=boots.price, delivery_fee=delivery.price,
                    total=str(float(boots.price) + float(delivery.price)),
                    delivery_method=delivery, payment_method=payment_method,
                )
                order.snapshot_address(address)
                order.save()
                item = OrderItem.objects.create(order=order, product=boots, qty=1, unit_price=boots.price)
                order.transition_to(Order.Status.SHIPPED)
                order.transition_to(Order.Status.OUT_FOR_DELIVERY)
                order.transition_to(Order.Status.DELIVERED)
                Review.objects.get_or_create(order_item=item, defaults=dict(user=reviewer, product=boots, rating=rating, comment=comment))

    def _seed_return_request(self, user):
        if ReturnRequest.objects.filter(user=user).exists():
            return

        order = Order.objects.filter(user=user, status=Order.Status.DELIVERED).first()
        if order is None:
            return
        item = order.items.first()
        if item is None:
            return

        try:
            request_return(
                order=order, user=user,
                reason="These run a size too small - would like a refund.",
                lines=[{"order_item_id": item.id, "qty": 1}],
            )
        except ReturnError:
            pass

    def _seed_notifications(self, user):
        specs = [
            (Notification.Type.ORDER_UPDATE, "Your order has shipped", "Order tracking has been updated - it's on its way."),
            (Notification.Type.PROMO, "Flash sale: up to 30% off", "This weekend only - don't miss out on top gear."),
            (Notification.Type.SYSTEM, "Welcome to SportTech", "Thanks for joining - explore gear from verified sellers."),
        ]
        for type_, title, body in specs:
            Notification.objects.get_or_create(user=user, title=title, defaults={"type": type_, "body": body})
