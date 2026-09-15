from unittest.mock import Mock, patch

import pytest

from core.sms import MNotifySMSBackend, SMSDeliveryError


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+233201234567", "0201234567"),
        ("233201234567", "0201234567"),
        ("0201234567", "0201234567"),
        ("201234567", "0201234567"),
        ("+233 20 123 4567", "0201234567"),
    ],
)
def test_to_local_ghana_format(raw, expected):
    assert MNotifySMSBackend._to_local_ghana_format(raw) == expected


@pytest.mark.django_db
@patch("requests.post")
def test_send_success_posts_local_format_and_message(mock_post, settings):
    settings.MNOTIFY_API_KEY = "test-key"
    settings.MNOTIFY_SENDER_ID = "SportShop"
    mock_post.return_value = Mock(status_code=200, json=lambda: {"status": "success"})
    mock_post.return_value.raise_for_status = Mock()

    MNotifySMSBackend().send("+233201234567", "Your code is 123456.")

    mock_post.assert_called_once()
    _, kwargs = mock_post.call_args
    assert kwargs["params"] == {"key": "test-key"}
    assert kwargs["json"]["recipient"] == ["0201234567"]
    assert kwargs["json"]["sender"] == "SportShop"
    assert kwargs["json"]["message"] == "Your code is 123456."


@pytest.mark.django_db
@patch("requests.post")
def test_send_raises_when_provider_reports_failure(mock_post, settings):
    settings.MNOTIFY_API_KEY = "test-key"
    mock_post.return_value = Mock(status_code=200, json=lambda: {"status": "error", "message": "Insufficient balance"})
    mock_post.return_value.raise_for_status = Mock()

    with pytest.raises(SMSDeliveryError, match="Insufficient balance"):
        MNotifySMSBackend().send("0201234567", "Hi")


@pytest.mark.django_db
def test_send_raises_without_api_key(settings):
    settings.MNOTIFY_API_KEY = ""

    with pytest.raises(SMSDeliveryError, match="not configured"):
        MNotifySMSBackend().send("0201234567", "Hi")
