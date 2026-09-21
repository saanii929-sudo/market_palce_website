"""Shared firebase_admin App for the whole project - both Google sign-in
verification (accounts.services.social) and push notifications
(notifications.push) need one, and firebase_admin raises if you try to
initialize two apps under the same name, so there's exactly one lazy
initializer here rather than one per caller."""

import json
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

_firebase_app = None


def _load_credential():
    from firebase_admin import credentials

    if settings.FIREBASE_CREDENTIALS_JSON:
        try:
            info = json.loads(settings.FIREBASE_CREDENTIALS_JSON)
        except ValueError:
            logger.error("FIREBASE_CREDENTIALS_JSON is set but isn't valid JSON - ignoring it.")
            return None
        return credentials.Certificate(info)

    if settings.FIREBASE_CREDENTIALS_PATH:
        return credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)

    return None


def get_firebase_app():
    """Returns the shared firebase_admin App, or None if neither
    FIREBASE_CREDENTIALS_JSON nor FIREBASE_CREDENTIALS_PATH is configured -
    callers decide how to degrade (push notifications silently no-op,
    Google sign-in raises a clear user-facing error)."""
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    import firebase_admin

    try:
        _firebase_app = firebase_admin.get_app("sportshop")
        return _firebase_app
    except ValueError:
        pass

    cred = _load_credential()
    if cred is None:
        return None

    _firebase_app = firebase_admin.initialize_app(cred, name="sportshop")
    return _firebase_app
