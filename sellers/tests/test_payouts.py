import datetime
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from catalog.tests.factories import CategoryFactory, ProductFactory, SellerFactory
from orders.models import Order
from orders.tests.factories import OrderItemFactory

from ..models import Payout
from ..services import (
    COMMISSION_INTRODUCED_ON,
    PayoutError,
    get_available_balance,
    mark_payout_paid,
    reject_payout,
    request_withdrawal,
    schedule_payout,
)


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def _seller_with_delivered_sales(amount="100.00", commission_rate=Decimal("0.00")):
    """A seller with one delivered order item, in a zero-commission category
    so plain balance assertions don't need to account for commission math -
    see test_commission.py-style tests below for commission-specific cases."""
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)
    category = CategoryFactory(commission_rate=commission_rate)
    product = ProductFactory(seller=seller, category=category, price=amount)
    OrderItemFactory(product=product, qty=1, unit_price=amount, order__status=Order.Status.DELIVERED)
    return seller, seller_user


@pytest.mark.django_db
def test_available_balance_reflects_delivered_orders_only():
    seller, _ = _seller_with_delivered_sales("100.00")
    product = ProductFactory(seller=seller, price="30.00")
    OrderItemFactory(product=product, qty=1, unit_price="30.00", order__status=Order.Status.PROCESSING)

    assert get_available_balance(seller) == 100


@pytest.mark.django_db
def test_request_withdrawal_via_api():
    seller, seller_user = _seller_with_delivered_sales("100.00")
    client = authed_client(seller_user)

    response = client.post(
        reverse("seller-payout-list"),
        {"amount": "60.00", "method": "MTN MoMo", "account_details": "0241234567"},
    )
    assert response.status_code == 201
    assert Payout.objects.filter(seller=seller, status=Payout.Status.REQUESTED, amount="60.00").exists()
    assert get_available_balance(seller) == 40


@pytest.mark.django_db
def test_cannot_request_more_than_available_balance():
    seller, seller_user = _seller_with_delivered_sales("50.00")
    client = authed_client(seller_user)

    response = client.post(
        reverse("seller-payout-list"),
        {"amount": "60.00", "method": "MTN MoMo", "account_details": "0241234567"},
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_have_two_pending_requests():
    seller, seller_user = _seller_with_delivered_sales("100.00")
    request_withdrawal(seller, amount=40, method="MTN MoMo", account_details="0241234567")

    with pytest.raises(PayoutError):
        request_withdrawal(seller, amount=10, method="MTN MoMo", account_details="0241234567")


@pytest.mark.django_db
def test_non_seller_cannot_request_payout():
    user = UserFactory()
    client = authed_client(user)

    response = client.post(
        reverse("seller-payout-list"),
        {"amount": "10.00", "method": "MTN MoMo", "account_details": "0241234567"},
    )
    assert response.status_code == 403


@pytest.mark.django_db
def test_seller_lists_only_own_payouts():
    seller, seller_user = _seller_with_delivered_sales("100.00")
    other_seller, _ = _seller_with_delivered_sales("100.00")
    request_withdrawal(seller, amount=10, method="MTN MoMo", account_details="0241234567")
    request_withdrawal(other_seller, amount=10, method="MTN MoMo", account_details="0241234567")

    client = authed_client(seller_user)
    response = client.get(reverse("seller-payout-list"))
    assert response.data["count"] == 1


@pytest.mark.django_db
def test_admin_can_schedule_pay_and_reject_payout():
    seller, _ = _seller_with_delivered_sales("100.00")
    payout = request_withdrawal(seller, amount=30, method="MTN MoMo", account_details="0241234567")

    schedule_payout(payout, payout_date="2026-01-01")
    payout.refresh_from_db()
    assert payout.status == Payout.Status.SCHEDULED

    mark_payout_paid(payout)
    payout.refresh_from_db()
    assert payout.status == Payout.Status.PAID

    with pytest.raises(PayoutError):
        mark_payout_paid(payout)


@pytest.mark.django_db
def test_admin_can_reject_a_requested_payout():
    seller, _ = _seller_with_delivered_sales("100.00")
    payout = request_withdrawal(seller, amount=30, method="MTN MoMo", account_details="0241234567")

    reject_payout(payout, admin_note="Suspicious account details")
    payout.refresh_from_db()
    assert payout.status == Payout.Status.REJECTED
    # Rejected requests free up the balance again.
    assert get_available_balance(seller) == 100


@pytest.mark.django_db
def test_commission_is_deducted_for_orders_delivered_after_cutoff():
    seller, _ = _seller_with_delivered_sales("100.00", commission_rate=Decimal("20.00"))
    assert get_available_balance(seller) == Decimal("80.00")


@pytest.mark.django_db
def test_commission_rate_varies_by_category():
    seller_user = UserFactory()
    seller = SellerFactory(user=seller_user)

    cheap_commission = CategoryFactory(commission_rate=Decimal("5.00"))
    steep_commission = CategoryFactory(commission_rate=Decimal("30.00"))
    product_a = ProductFactory(seller=seller, category=cheap_commission, price="100.00")
    product_b = ProductFactory(seller=seller, category=steep_commission, price="100.00")
    OrderItemFactory(product=product_a, qty=1, unit_price="100.00", order__status=Order.Status.DELIVERED)
    OrderItemFactory(product=product_b, qty=1, unit_price="100.00", order__status=Order.Status.DELIVERED)

    # 100 * 0.95 + 100 * 0.70 = 165.00
    assert get_available_balance(seller) == Decimal("165.00")


@pytest.mark.django_db
def test_orders_delivered_before_commission_cutoff_are_grandfathered_at_full_gross():
    seller, _ = _seller_with_delivered_sales("100.00", commission_rate=Decimal("20.00"))
    order = seller.products.first().order_items.first().order
    day_before_cutoff = timezone.make_aware(
        datetime.datetime.combine(COMMISSION_INTRODUCED_ON - datetime.timedelta(days=1), datetime.time.min)
    )
    Order.objects.filter(pk=order.pk).update(updated_at=day_before_cutoff)

    assert get_available_balance(seller) == Decimal("100.00")
