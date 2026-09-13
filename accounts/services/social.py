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
