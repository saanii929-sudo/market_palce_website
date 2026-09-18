"""Verifies provider tokens for social sign-in and returns normalized profile data.

{"email": str | None, "full_name": str, "provider_id": str}
"""
import jwt
from django.conf import settings
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token


class SocialAuthError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def verify_google_token(token: str) -> dict:
    try:
        payload = google_id_token.verify_oauth2_token(
            token, google_requests.Request(), settings.GOOGLE_OAUTH_CLIENT_ID
        )
    except ValueError as exc:
        raise SocialAuthError("Invalid Google token") from exc

    return {
        "email": payload.get("email"),
        "full_name": payload.get("name", ""),
        "provider_id": payload["sub"],
    }


_firebase_app = None


def _get_firebase_app():
    """Lazily initializes the Firebase Admin app from a service-account key
    file, once per process. Kept behind a function (rather than module-level
    initialization) so importing this module never requires credentials to
    be configured - only actually calling verify_firebase_token does, and
    tests can monkeypatch this to avoid needing real credentials at all."""
    global _firebase_app
    if _firebase_app is None:
        if not settings.FIREBASE_CREDENTIALS_PATH:
            raise SocialAuthError("Google sign-in via Firebase is not configured on the server.")

        import firebase_admin
        from firebase_admin import credentials

        cred = credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH)
        _firebase_app = firebase_admin.initialize_app(cred, name="sportshop")
    return _firebase_app


def verify_firebase_token(token: str) -> dict:
    """Verifies a Firebase ID token - what the Flutter app gets back from
    firebase_auth after GoogleAuthProvider sign-in - as opposed to
    verify_google_token(), which verifies a raw Google OAuth id_token
    (different issuer/audience, since Firebase re-signs its own tokens)."""
    from firebase_admin import auth as firebase_auth
    from firebase_admin.exceptions import FirebaseError

    try:
        payload = firebase_auth.verify_id_token(token, app=_get_firebase_app())
    except SocialAuthError:
        raise
    except (FirebaseError, ValueError) as exc:
        raise SocialAuthError("Invalid Firebase token") from exc

    provider = payload.get("firebase", {}).get("sign_in_provider")
    if provider != "google.com":
        raise SocialAuthError("This sign-in method only accepts Google-authenticated Firebase tokens.")

    return {
        "email": payload.get("email"),
        "full_name": payload.get("name", ""),
        "provider_id": payload["uid"],
    }


APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_apple_jwk_client = None


def _get_apple_jwk_client():
    global _apple_jwk_client
    if _apple_jwk_client is None:
        _apple_jwk_client = jwt.PyJWKClient(APPLE_JWKS_URL)
    return _apple_jwk_client


def verify_apple_token(token: str) -> dict:
    try:
        signing_key = _get_apple_jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.APPLE_CLIENT_ID,
            issuer="https://appleid.apple.com",
        )
    except jwt.PyJWTError as exc:
        raise SocialAuthError("Invalid Apple token") from exc

    return {
        "email": payload.get("email"),
        "full_name": "",
        "provider_id": payload["sub"],
    }
