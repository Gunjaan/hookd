"""Signup, login and logout pages (session auth)."""

from django.conf import settings
from django.contrib.auth import login, logout
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.accounts import services
from apps.accounts.forms import AccessRequestForm, LoginForm, SignupForm
from apps.accounts.model_querysets import create_user


def _safe_next(request) -> str:
    """The ?next= target if it points back into this site, otherwise the default page."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return target
    return settings.LOGIN_REDIRECT_URL


def login_page(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    form = LoginForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        login(request, form.user)
        return redirect(_safe_next(request))
    return render(
        request,
        "accounts/login.html",
        {"form": form, "next": request.GET.get("next", ""), "signups_open": settings.SIGNUPS_OPEN},
    )


def signup_page(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request))
    if not settings.SIGNUPS_OPEN:
        return _access_request_page(request)
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = create_user(**form.cleaned_data)
        login(request, user)
        return redirect(_safe_next(request))
    return render(request, "accounts/signup.html", {"form": form, "next": request.GET.get("next", "")})


def _access_request_page(request):
    """Signup while it is closed: collect the applicant's details and email them to support."""
    form = AccessRequestForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.request_access(form.cleaned_data)
        return redirect("access-requested")
    return render(request, "accounts/request_access.html", {"form": form})


def access_requested_page(request):
    return render(request, "accounts/access_requested.html")


@require_POST
def logout_page(request):
    logout(request)
    return redirect("home")
