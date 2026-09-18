import pytest
from django.urls import reverse

from .test_catalog_management import seller_client
from .test_pos_variants import clocked_in_client


@pytest.mark.django_db
def test_pos_terminal_renders_calculator_when_clocked_in():
    _, seller = seller_client(subscribed=True)
    client, _ = clocked_in_client(seller)

    response = client.get(reverse("web-pos-terminal"))
    assert response.status_code == 200
    content = response.content.decode()
    assert 'id="calculator-open-btn"' in content
    assert 'id="calculator-panel"' in content
    assert 'id="icon-calculator"' in content


@pytest.mark.django_db
def test_pos_terminal_hides_calculator_before_clock_in():
    client, _ = seller_client(subscribed=True)

    response = client.get(reverse("web-pos-terminal"))
    assert response.status_code == 200
    assert 'id="calculator-open-btn"' not in response.content.decode()
