from django import forms
from django.contrib.auth import authenticate
from django.contrib.auth.forms import BaseUserCreationForm, UserChangeForm as BaseUserChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from apps.accounts.model_querysets import email_is_registered
from apps.accounts.models import User


class UserCreationForm(BaseUserCreationForm):
    """Admin form."""

    class Meta(BaseUserCreationForm.Meta):
        model = User
        fields = ("email", "name")


class UserChangeForm(BaseUserChangeForm):
    """Admin form."""

    class Meta(BaseUserChangeForm.Meta):
        model = User
        fields = "__all__"


class SignupForm(forms.Form):
    name = forms.CharField(
        label="Name",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Your creator name"}),
    )
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        label="Password",
        strip=False,
        help_text="At least 8 characters.",
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def clean_email(self) -> str:
        email = self.cleaned_data["email"].lower()
        if email_is_registered(email):
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        if "email" in cleaned and cleaned.get("password"):
            candidate = User(email=cleaned["email"], name=cleaned.get("name", ""))
            try:
                validate_password(cleaned["password"], user=candidate)
            except ValidationError as exc:
                self.add_error("password", exc)
        return cleaned


class AccessRequestForm(forms.Form):
    """Shown on /signup/ while SIGNUPS_OPEN is false. Emailed to the support inbox."""

    name = forms.CharField(
        label="Name",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Your name or brand"}),
    )
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    instagram = forms.CharField(
        label="Instagram handle",
        max_length=100,
        widget=forms.TextInput(attrs={"placeholder": "@yourhandle"}),
    )
    phone = forms.CharField(
        label="Phone or WhatsApp",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"autocomplete": "tel", "placeholder": "Optional"}),
    )
    about = forms.CharField(
        label="What do you create or sell?",
        max_length=1000,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "Handmade jewellery, fitness coaching, food reels..."}),
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].lower()

    def clean_instagram(self) -> str:
        return "@" + self.cleaned_data["instagram"].strip().lstrip("@")


class LoginForm(forms.Form):
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )

    def __init__(self, *args, request=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.request = request
        self.user = None

    def clean(self):
        cleaned = super().clean()
        if "email" in cleaned and cleaned.get("password"):
            self.user = authenticate(self.request, email=cleaned["email"].lower(), password=cleaned["password"])
            if self.user is None:
                raise ValidationError("Invalid email or password.", code="invalid_credentials")
        return cleaned
