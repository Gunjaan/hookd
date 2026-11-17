"""JSON responses for the `fetch` endpoints under /api/ (see AGENTS.md). Session auth and CSRF stay on."""

from functools import wraps

from django.http import JsonResponse


def json_ok(**data) -> JsonResponse:
    return JsonResponse(data)


def json_error(message: str, status: int = 400) -> JsonResponse:
    return JsonResponse({"error": message}, status=status)


def api_login_required(view):
    """Like login_required, but answers 401 JSON instead of redirecting a fetch call to the login page."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return json_error("Please log in again.", status=401)
        return view(request, *args, **kwargs)

    return wrapper
