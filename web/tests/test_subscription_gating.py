import pytest
from django.urls import reverse

from sellers.models import SellerSubscription, SubscriptionPlan

from .test_catalog_management import seller_client


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", [
    "web-pos-terminal",
    "web-seller-inventory",
    "web-seller-suppliers",
    "web-seller-employees",
    "web-seller-payroll",
    "web-seller-customers",
    "web-seller-discounts",
    "web-seller-reports",
    "web-seller-invoices",
    "web-seller-warehouse",
])
def test_unsubscribed_seller_is_redirected_away_from_pos_suite(url_name):
    client, _ = seller_client()
    response = client.get(reverse(url_name))
    assert response.status_code == 302
    assert response.url == reverse("web-seller-subscription")


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", [
    "web-pos-terminal",
    "web-seller-inventory",
    "web-seller-employees",
    "web-seller-warehouse",
])
def test_subscribed_seller_can_access_pos_suite(url_name):
    client, _ = seller_client(subscribed=True)
    response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", [
    "web-seller-overview",
    "web-seller-products",
    "web-seller-orders",
    "web-seller-payouts",
    "web-seller-settings",
])
def test_store_pages_stay_free_without_a_subscription(url_name):
    client, _ = seller_client()
    response = client.get(reverse(url_name))
    assert response.status_code == 200


@pytest.mark.django_db
def test_subscription_page_lists_active_plans_and_current_status():
    client, seller = seller_client(subscribed=True)
    SubscriptionPlan.objects.create(name="Starter", slug="starter", price="45.00")

    response = client.get(reverse("web-seller-subscription"))
    assert response.status_code == 200
    assert b"Starter" in response.content
    content = response.content.decode()
    assert "Active" in content


@pytest.mark.django_db
def test_expired_subscription_no_longer_grants_access():
    from django.utils import timezone

    client, seller = seller_client()
    plan = SubscriptionPlan.objects.create(name="Growth", slug="growth", price="120.00")
    SellerSubscription.objects.create(
        seller=seller, plan=plan, amount=plan.price, status=SellerSubscription.Status.ACTIVE,
        starts_at=timezone.now() - timezone.timedelta(days=40),
        expires_at=timezone.now() - timezone.timedelta(days=10),
    )

    response = client.get(reverse("web-pos-terminal"))
    assert response.status_code == 302
    assert response.url == reverse("web-seller-subscription")
