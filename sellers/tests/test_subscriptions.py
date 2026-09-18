from unittest.mock import Mock, patch

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from catalog.tests.factories import SellerFactory

from ..models import SellerSubscription, SubscriptionPlan
from ..services import (
    SubscriptionError,
    finalize_subscription_payment,
    get_active_subscription,
    mark_subscription_failed,
    start_subscription_checkout,
)


def _hubtel_initiate_response(reference="SUB-TEST", checkout_url="https://pay.hubtel.com/checkout/sub"):
    return Mock(
        status_code=200,
        json=lambda: {
            "responseCode": "0000",
            "data": {"checkoutUrl": checkout_url, "clientReference": reference},
        },
        raise_for_status=Mock(),
    )


def _hubtel_status_response(hubtel_status="Paid"):
    return Mock(
        status_code=200,
        json=lambda: {"responseCode": "0000", "data": {"status": hubtel_status}},
        raise_for_status=Mock(),
    )


@pytest.mark.django_db
def test_seller_without_subscription_has_none_active():
    seller = SellerFactory()
    assert get_active_subscription(seller) is None


@pytest.mark.django_db
@patch("requests.post")
def test_start_subscription_checkout_creates_pending_subscription(mock_post, settings):
    settings.HUBTEL_API_ID = "id"
    settings.HUBTEL_API_KEY = "key"
    settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
    mock_post.return_value = _hubtel_initiate_response()

    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")

    subscription = start_subscription_checkout(
        seller, plan, callback_url="https://x/cb", return_url="https://x/return", cancellation_url="https://x/cancel",
    )

    assert subscription.status == SellerSubscription.Status.PENDING
    assert subscription.checkout_url == "https://pay.hubtel.com/checkout/sub"
    assert get_active_subscription(seller) is None


@pytest.mark.django_db
def test_start_subscription_checkout_rejects_inactive_plan():
    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Retired", slug="retired", price="50.00", is_active=False)

    with pytest.raises(SubscriptionError):
        start_subscription_checkout(
            seller, plan, callback_url="https://x/cb", return_url="https://x/return", cancellation_url="https://x/cancel",
        )


@pytest.mark.django_db
def test_finalize_subscription_payment_activates_it():
    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00", billing_period_days=30)
    subscription = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price)

    finalize_subscription_payment(subscription)
    subscription.refresh_from_db()

    assert subscription.status == SellerSubscription.Status.ACTIVE
    assert get_active_subscription(seller) == subscription
    expected_expiry = subscription.starts_at + timezone.timedelta(days=30)
    assert abs((subscription.expires_at - expected_expiry).total_seconds()) < 2


@pytest.mark.django_db
def test_finalize_subscription_payment_is_idempotent():
    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    subscription = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price)

    finalize_subscription_payment(subscription)
    first_expiry = SellerSubscription.objects.get(pk=subscription.pk).expires_at

    finalize_subscription_payment(subscription)
    assert SellerSubscription.objects.get(pk=subscription.pk).expires_at == first_expiry


@pytest.mark.django_db
def test_renewing_before_expiry_extends_from_current_expiry_not_now():
    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00", billing_period_days=30)
    current = SellerSubscription.objects.create(
        seller=seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.ACTIVE,
        starts_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(days=10),
    )

    renewal = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price)
    finalize_subscription_payment(renewal)
    renewal.refresh_from_db()

    # Extends from the still-active subscription's expiry, not from "now" -
    # an early renewal shouldn't waste already-paid-for days.
    expected = current.expires_at + timezone.timedelta(days=30)
    assert abs((renewal.expires_at - expected).total_seconds()) < 2


@pytest.mark.django_db
def test_mark_subscription_failed():
    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    subscription = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price)

    mark_subscription_failed(subscription, "Card declined.")
    subscription.refresh_from_db()

    assert subscription.status == SellerSubscription.Status.FAILED
    assert subscription.failure_reason == "Card declined."


@pytest.mark.django_db
@patch("requests.get")
def test_subscription_webhook_activates_on_success(mock_get, settings):
    settings.HUBTEL_API_ID = "id"
    settings.HUBTEL_API_KEY = "key"
    settings.HUBTEL_MERCHANT_ACCOUNT = "merchant"
    mock_get.return_value = _hubtel_status_response("Paid")

    seller = SellerFactory()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    subscription = SellerSubscription.objects.create(seller=seller, plan=plan, amount=plan.price, reference="SUB-WEBHOOK")

    client = APIClient()
    response = client.post(
        reverse("seller-subscription-webhook"),
        {"Data": {"ClientReference": "SUB-WEBHOOK", "Status": "Success"}},
        format="json",
    )
    assert response.status_code == 200

    subscription.refresh_from_db()
    assert subscription.status == SellerSubscription.Status.ACTIVE
