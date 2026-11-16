from django.urls import path

from apps.accounts.views import access_requested_page, login_page, logout_page, signup_page

urlpatterns = [
    path("login/", login_page, name="login"),
    path("signup/", signup_page, name="signup"),
    path("signup/requested/", access_requested_page, name="access-requested"),
    path("logout/", logout_page, name="logout"),
]
