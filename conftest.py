import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def force_console_sms_backend(settings):
    """Whatever SMS_BACKEND is configured for real deployments (e.g. mNotify),
    tests must never make a live network call to a real SMS provider - mirrors
    how Django's own test runner always forces email to a local backend."""
    settings.SMS_BACKEND = "core.sms.ConsoleSMSBackend"


@pytest.fixture(autouse=True)
def force_local_media_storage(settings, tmp_path):
    """Whatever STORAGES is configured for real deployments (e.g. R2/S3),
    tests must never upload real files to a live bucket - file-upload tests
    (e.g. seller application documents) write to a throwaway temp dir instead."""
    settings.STORAGES = {**settings.STORAGES, "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}}
    settings.MEDIA_ROOT = str(tmp_path)
