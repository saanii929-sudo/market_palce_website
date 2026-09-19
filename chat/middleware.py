from urllib.parse import parse_qs

from channels.db import database_sync_to_async


@database_sync_to_async
def _get_user_from_token(token: str):
    from accounts.models import User
    from rest_framework_simplejwt.exceptions import TokenError
    from rest_framework_simplejwt.tokens import AccessToken

    try:
        validated = AccessToken(token)
        return User.objects.get(id=validated["user_id"])
    except (TokenError, User.DoesNotExist, KeyError):
        return None


class JWTAuthMiddleware:
    """Lets a WebSocket connection authenticate with `?token=<JWT access
    token>` in the query string - what the Flutter app has, since it has no
    Django session cookie. Must sit *inside* Channels' AuthMiddlewareStack
    (i.e. AuthMiddlewareStack(JWTAuthMiddleware(...)), not the other way
    round) so it runs after - and can override - the session-based user
    AuthMiddlewareStack already resolved for the browser-based web app,
    without a valid token ever clobbering a legitimate session login."""

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        query_string = scope.get("query_string", b"").decode()
        token = parse_qs(query_string).get("token", [None])[0]
        if token:
            user = await _get_user_from_token(token)
            if user is not None:
                scope["user"] = user
        return await self.inner(scope, receive, send)
