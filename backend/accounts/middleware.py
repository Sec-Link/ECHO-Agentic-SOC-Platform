from __future__ import annotations

from django.http import JsonResponse

from .services import should_deny_write_for_readonly_user


class ReadonlyWriteBlockMiddleware:
    """
    Deny non-safe API methods for readonly users across all modules.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or ""
        if path.startswith("/api/v1/"):
            user = getattr(request, "user", None)
            if should_deny_write_for_readonly_user(user, path=path, method=request.method):
                return JsonResponse(
                    {"detail": "Readonly users cannot modify data."},
                    status=403,
                )
        return self.get_response(request)
