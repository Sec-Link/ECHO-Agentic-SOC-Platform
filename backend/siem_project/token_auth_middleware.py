from __future__ import annotations

from typing import Callable


class DRFTokenAuthMiddleware:
    """
    Allow Django (session-based) views protected by @login_required to accept
    DRF TokenAuthentication headers: `Authorization: Token <key>`.

    This is intentionally narrow and only applies when the request is not
    already authenticated via session/cookies.
    """

    def __init__(self, get_response: Callable):
        self.get_response = get_response

    def __call__(self, request):
        try:
            user = getattr(request, "user", None)
            if user is None or not getattr(user, "is_authenticated", False):
                auth = request.META.get("HTTP_AUTHORIZATION", "")
                if isinstance(auth, str) and auth.startswith("Token "):
                    key = auth.split(" ", 1)[1].strip()
                    if key:
                        from rest_framework.authtoken.models import Token

                        token = Token.objects.select_related("user").filter(key=key).first()
                        if token and token.user:
                            request.user = token.user
                            request.auth = token
                            # Keep Django's cached user in sync if present.
                            setattr(request, "_cached_user", token.user)
        except Exception:
            # Never break the request lifecycle due to auth parsing.
            pass

        return self.get_response(request)

