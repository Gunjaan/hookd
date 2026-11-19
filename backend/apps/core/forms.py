"""Forms for the public pages."""

from django import forms


class ContactForm(forms.Form):
    """The landing page contact form. Emailed to the support inbox."""

    name = forms.CharField(
        label="Name",
        max_length=150,
        widget=forms.TextInput(attrs={"autocomplete": "name", "placeholder": "Your name"}),
    )
    email = forms.EmailField(label="Email", widget=forms.EmailInput(attrs={"autocomplete": "email"}))
    message = forms.CharField(
        label="Message",
        max_length=2000,
        widget=forms.Textarea(attrs={"rows": 4, "placeholder": "A question, a walkthrough, anything"}),
    )

    def clean_email(self) -> str:
        return self.cleaned_data["email"].lower()
