import datetime
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from catalog.models import Seller

from .models import Payout, PayoutAccount, SellerApplication, SellerSubscription, SubscriptionPlan
from .notifications import (
    notify_application_reviewed,
    notify_kyc_resolved,
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


def submit_kyc(
    application: SellerApplication, *,
    bank_account_name: str = "", bank_account_number: str = "", bank_name: str = "",
    momo_number: str = "", momo_network: str = "",
) -> SellerApplication:
    if application.status != SellerApplication.Status.APPROVED:
        raise SellerApplicationError("KYC can only be submitted once your seller application is approved.")
    if not (bank_account_number.strip() or momo_number.strip()):
        raise SellerApplicationError("Provide either bank account details or a mobile money number.")

    application.bank_account_name = bank_account_name.strip()
    application.bank_account_number = bank_account_number.strip()
    application.bank_name = bank_name.strip()
    application.momo_number = momo_number.strip()
    application.momo_network = momo_network.strip()
    application.kyc_status = SellerApplication.KYCStatus.PENDING
    application.kyc_reviewed_at = None
    application.save(update_fields=[
        "bank_account_name", "bank_account_number", "bank_name",
        "momo_number", "momo_network", "kyc_status", "kyc_reviewed_at",
    ])
    return application


@transaction.atomic
def verify_kyc(application: SellerApplication) -> SellerApplication:
    from orders.services.payment_gateway import PaymentGatewayError, get_gateway

    if application.kyc_status == SellerApplication.KYCStatus.VERIFIED:
        raise SellerApplicationError("This application's KYC is already verified.")
    if not (application.bank_account_number or application.momo_number):
        raise SellerApplicationError("No payout details have been submitted yet.")

    seller = getattr(application.user, "seller_profile", None)
    if seller is None:
        raise SellerApplicationError("This user doesn't have a seller account yet.")

    if application.momo_number:
        payout_type = PayoutAccount.Type.MOMO
        account_number, bank_code = application.momo_number, application.momo_network
    else:
        payout_type = PayoutAccount.Type.BANK
        account_number, bank_code = application.bank_account_number, application.bank_name

    gateway_name = getattr(settings, "PAYMENT_DEFAULT_GATEWAY", "paystack")
    try:
        reference = get_gateway(gateway_name).tokenize_payout_destination(
            type=payout_type, account_number=account_number, bank_code=bank_code,
            account_name=application.bank_account_name,
        )
    except PaymentGatewayError as exc:
        raise SellerApplicationError(str(exc)) from exc

    PayoutAccount.objects.filter(seller=seller, is_active=True).update(is_active=False)
    PayoutAccount.objects.create(seller=seller, type=payout_type, account_reference=reference)

    application.kyc_status = SellerApplication.KYCStatus.VERIFIED
    application.kyc_reviewed_at = timezone.now()
    application.save(update_fields=["kyc_status", "kyc_reviewed_at"])

    notify_kyc_resolved(application)
    return application


def reject_kyc(application: SellerApplication, reviewer_note: str = "") -> SellerApplication:
    if application.kyc_status == SellerApplication.KYCStatus.VERIFIED:
        raise SellerApplicationError("This application's KYC is already verified.")

    application.kyc_status = SellerApplication.KYCStatus.REJECTED
    application.kyc_reviewed_at = timezone.now()
    if reviewer_note:
        application.reviewer_note = reviewer_note
    application.save(update_fields=["kyc_status", "kyc_reviewed_at", "reviewer_note"])

    notify_kyc_resolved(application)
    return application


COMMISSION_INTRODUCED_ON = datetime.date(2026, 9, 15)


def _net_line_total(item) -> Decimal:
    gross = item.unit_price * item.qty
    if item.seller_order.updated_at.date() < COMMISSION_INTRODUCED_ON:
        return gross
    rate = item.product.category.commission_rate
    return (gross * (Decimal("100") - rate) / Decimal("100")).quantize(Decimal("0.01"))


def get_lifetime_earnings(seller: Seller) -> Decimal:
    from orders.models import OrderItem, SellerOrder

    items = OrderItem.objects.filter(
        product__seller=seller, seller_order__status=SellerOrder.Status.DELIVERED
    ).select_related("product__category", "seller_order")
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
    if not PayoutAccount.objects.filter(seller=payout.seller, is_active=True).exists():
        raise PayoutError("This seller hasn't completed KYC verification - no payout destination on file.")

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
