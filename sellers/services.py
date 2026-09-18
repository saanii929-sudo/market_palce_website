import datetime
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from catalog.models import Seller

from .models import Payout, SellerApplication, SellerSubscription, SubscriptionPlan
from .notifications import (
    notify_application_reviewed,
    notify_payout_requested,
    notify_payout_resolved,
    notify_subscription_activated,
    notify_subscription_failed,
)


class SellerApplicationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class PayoutError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def submit_application(
    user, *, business_name: str, category, phone: str, id_document=None, business_certificate=None
) -> SellerApplication:
    if SellerApplication.objects.filter(user=user, status=SellerApplication.Status.PENDING).exists():
        raise SellerApplicationError("You already have a pending seller application.")
    if user.role == User.Role.SELLER:
        raise SellerApplicationError("You're already a seller.")
    if id_document is None:
        raise SellerApplicationError("Please upload a government-issued ID to apply.")

    return SellerApplication.objects.create(
        user=user, business_name=business_name, category=category, phone=phone,
        id_document=id_document, business_certificate=business_certificate,
    )


def _unique_seller_slug(business_name: str) -> str:
    base = slugify(business_name) or "seller"
    slug = base
    suffix = 1
    while Seller.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


@transaction.atomic
def approve_application(application: SellerApplication, reviewer_note: str = "") -> Seller:
    if application.status != SellerApplication.Status.PENDING:
        raise SellerApplicationError("Only pending applications can be approved.")

    application.status = SellerApplication.Status.APPROVED
    application.reviewed_at = timezone.now()
    application.reviewer_note = reviewer_note
    application.save(update_fields=["status", "reviewed_at", "reviewer_note"])

    application.user.role = User.Role.SELLER
    application.user.save(update_fields=["role"])

    seller = Seller.objects.create(
        user=application.user,
        business_name=application.business_name,
        slug=_unique_seller_slug(application.business_name),
    )

    notify_application_reviewed(application)
    return seller


def reject_application(application: SellerApplication, reviewer_note: str = "") -> SellerApplication:
    if application.status != SellerApplication.Status.PENDING:
        raise SellerApplicationError("Only pending applications can be rejected.")

    application.status = SellerApplication.Status.REJECTED
    application.reviewed_at = timezone.now()
    application.reviewer_note = reviewer_note
    application.save(update_fields=["status", "reviewed_at", "reviewer_note"])

    notify_application_reviewed(application)
    return application


COMMISSION_INTRODUCED_ON = datetime.date(2026, 9, 15)


def _net_line_total(item) -> Decimal:
    gross = item.unit_price * item.qty
    if item.order.updated_at.date() < COMMISSION_INTRODUCED_ON:
        return gross
    rate = item.product.category.commission_rate
    return (gross * (Decimal("100") - rate) / Decimal("100")).quantize(Decimal("0.01"))


def get_lifetime_earnings(seller: Seller) -> Decimal:
    """Net revenue from this seller's delivered order items, after platform
    commission per product category (see COMMISSION_INTRODUCED_ON)."""
    from orders.models import Order, OrderItem

    items = OrderItem.objects.filter(
        product__seller=seller, order__status=Order.Status.DELIVERED
    ).select_related("product__category", "order")
    return sum((_net_line_total(item) for item in items), Decimal("0.00"))


def get_available_balance(seller: Seller) -> Decimal:
    committed = Payout.objects.filter(
        seller=seller,
        status__in=[Payout.Status.REQUESTED, Payout.Status.SCHEDULED, Payout.Status.PAID],
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0.00")

    return max(get_lifetime_earnings(seller) - committed, Decimal("0.00"))


def request_withdrawal(seller: Seller, *, amount: Decimal, method: str, account_details: str) -> Payout:
    method = (method or "").strip()
    account_details = (account_details or "").strip()

    if amount is None or amount <= 0:
        raise PayoutError("Enter a valid amount to withdraw.")
    if not method:
        raise PayoutError("Select a payout method.")
    if not account_details:
        raise PayoutError("Enter the account details to receive your payout.")
    if Payout.objects.filter(seller=seller, status=Payout.Status.REQUESTED).exists():
        raise PayoutError("You already have a withdrawal request awaiting review.")

    available = get_available_balance(seller)
    if amount > available:
        raise PayoutError(f"You can withdraw up to GH₵{available}.")

    payout = Payout.objects.create(
        seller=seller, amount=amount, method=method, account_details=account_details,
        status=Payout.Status.REQUESTED,
    )
    notify_payout_requested(payout)
    return payout


@transaction.atomic
def schedule_payout(payout: Payout, payout_date, admin_note: str = "") -> Payout:
    if payout.status != Payout.Status.REQUESTED:
        raise PayoutError("Only requested payouts can be scheduled.")

    payout.status = Payout.Status.SCHEDULED
    payout.payout_date = payout_date
    payout.admin_note = admin_note
    payout.save(update_fields=["status", "payout_date", "admin_note"])
    notify_payout_resolved(payout)
    return payout


@transaction.atomic
def mark_payout_paid(payout: Payout) -> Payout:
    if payout.status not in (Payout.Status.REQUESTED, Payout.Status.SCHEDULED):
        raise PayoutError("Only requested or scheduled payouts can be marked as paid.")

    payout.status = Payout.Status.PAID
    payout.payout_date = payout.payout_date or timezone.now().date()
    payout.save(update_fields=["status", "payout_date"])
    notify_payout_resolved(payout)
    return payout


@transaction.atomic
def reject_payout(payout: Payout, admin_note: str = "") -> Payout:
    if payout.status != Payout.Status.REQUESTED:
        raise PayoutError("Only requested payouts can be rejected.")

    payout.status = Payout.Status.REJECTED
    payout.admin_note = admin_note
    payout.save(update_fields=["status", "admin_note"])
    notify_payout_resolved(payout)
    return payout


class SubscriptionError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def get_active_subscription(seller: Seller) -> SellerSubscription | None:
    now = timezone.now()
    SellerSubscription.objects.filter(
        seller=seller, status=SellerSubscription.Status.ACTIVE, expires_at__lte=now
    ).update(status=SellerSubscription.Status.EXPIRED)

    return (
        SellerSubscription.objects.filter(
            seller=seller, status=SellerSubscription.Status.ACTIVE, expires_at__gt=now
        )
        .order_by("-expires_at")
        .first()
    )


def start_subscription_checkout(
    seller: Seller, plan: SubscriptionPlan, *, callback_url: str, return_url: str, cancellation_url: str
) -> SellerSubscription:
    from orders.services.payment_gateway import HubtelGateway, PaymentGatewayError

    if not plan.is_active:
        raise SubscriptionError("This plan is no longer available.")

    subscription = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price)

    try:
        result = HubtelGateway().initiate_checkout(
            reference=subscription.reference,
            amount=subscription.amount,
            description=f"SportShop {plan.name} subscription for {seller.business_name}",
            callback_url=callback_url,
            return_url=return_url,
            cancellation_url=cancellation_url,
        )
    except PaymentGatewayError as exc:
        subscription.status = SellerSubscription.Status.FAILED
        subscription.failure_reason = str(exc)
        subscription.save(update_fields=["status", "failure_reason"])
        raise SubscriptionError(str(exc)) from exc

    subscription.checkout_url = result["authorization_url"] or ""
    subscription.save(update_fields=["checkout_url"])
    return subscription


@transaction.atomic
def finalize_subscription_payment(subscription: SellerSubscription) -> SellerSubscription:
    subscription = SellerSubscription.objects.select_for_update().get(pk=subscription.pk)
    if subscription.status != SellerSubscription.Status.PENDING:
        return subscription

    current = get_active_subscription(subscription.seller)
    start_from = current.expires_at if current else timezone.now()

    subscription.status = SellerSubscription.Status.ACTIVE
    subscription.starts_at = timezone.now()
    subscription.expires_at = start_from + datetime.timedelta(days=subscription.plan.billing_period_days)
    subscription.save(update_fields=["status", "starts_at", "expires_at"])

    notify_subscription_activated(subscription)
    return subscription


def mark_subscription_failed(subscription: SellerSubscription, reason: str) -> SellerSubscription:
    if subscription.status != SellerSubscription.Status.PENDING:
        return subscription
    subscription.status = SellerSubscription.Status.FAILED
    subscription.failure_reason = reason
    subscription.save(update_fields=["status", "failure_reason"])
    notify_subscription_failed(subscription)
    return subscription
