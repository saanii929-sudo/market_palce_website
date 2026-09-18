import pytest
from django.urls import reverse
from django.utils import timezone

from catalog.tests.factories import SellerFactory
from sellers.models import SellerSubscription, SubscriptionPlan

from .test_catalog_management import admin_client, seller_client


@pytest.mark.django_db
def test_admin_can_see_active_subscriptions_in_console():
    client, _ = admin_client()
    seller = SellerFactory(business_name="Northmark")
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    SellerSubscription.objects.create(
        seller=seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.ACTIVE,
        starts_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(days=30),
    )

    response = client.get(reverse("web-console-subscriptions"))
    assert response.status_code == 200
    assert b"Northmark" in response.content
    assert b"Growth" in response.content


@pytest.mark.django_db
def test_console_subscriptions_status_tabs_filter_correctly():
    client, _ = admin_client()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    active_seller = SellerFactory(business_name="ActiveCo")
    failed_seller = SellerFactory(business_name="FailedCo")
    SellerSubscription.objects.create(
        seller=active_seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.ACTIVE,
        starts_at=timezone.now(), expires_at=timezone.now() + timezone.timedelta(days=30),
    )
    SellerSubscription.objects.create(
        seller=failed_seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.FAILED,
    )

    active_response = client.get(reverse("web-console-subscriptions"), {"status": "active"})
    assert b"ActiveCo" in active_response.content
    assert b"FailedCo" not in active_response.content

    failed_response = client.get(reverse("web-console-subscriptions"), {"status": "failed"})
    assert b"FailedCo" in failed_response.content
    assert b"ActiveCo" not in failed_response.content


@pytest.mark.django_db
def test_non_admin_cannot_access_console_subscriptions():
    client, _ = seller_client()
    response = client.get(reverse("web-console-subscriptions"))
    assert response.status_code == 302
