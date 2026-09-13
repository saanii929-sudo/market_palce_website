from datetime import timedelta

import pytest
from django.utils import timezone

from catalog.tasks import deactivate_expired_flash_deals

from .factories import FlashDealFactory


@pytest.mark.django_db
def test_percent_stock_sold_computation():
    deal = FlashDealFactory(stock_qty=70, stock_sold=30)
    assert deal.percent_stock_sold == 30


@pytest.mark.django_db
def test_percent_stock_sold_zero_when_no_stock_moved():
    deal = FlashDealFactory(stock_qty=0, stock_sold=0)
    assert deal.percent_stock_sold == 0


@pytest.mark.django_db
def test_deactivate_expired_flash_deals_flips_ended_deals():
    now = timezone.now()
    expired = FlashDealFactory(
        starts_at=now - timedelta(days=2), ends_at=now - timedelta(days=1), is_active=True
    )
    sold_out = FlashDealFactory(
        starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1), stock_qty=0, is_active=True
    )
    live = FlashDealFactory(
        starts_at=now - timedelta(hours=1), ends_at=now + timedelta(hours=1), stock_qty=5, is_active=True
    )

    deactivate_expired_flash_deals()

    expired.refresh_from_db()
    sold_out.refresh_from_db()
    live.refresh_from_db()
    assert expired.is_active is False
    assert sold_out.is_active is False
    assert live.is_active is True
