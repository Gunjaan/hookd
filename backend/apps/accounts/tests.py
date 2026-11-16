from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user, email_is_registered


class AccessRequestTests(TestCase):
    """Signup is closed (SIGNUPS_OPEN = False): /signup/ collects details and emails support."""

    def test_signup_page_shows_access_request_form(self):
        response = self.client.get(reverse("signup"))
        self.assertContains(response, "Request early access")
        self.assertNotContains(response, 'name="password"')

    def test_request_emails_support_and_creates_no_user(self):
        response = self.client.post(
            reverse("signup"),
            {
                "name": "Asha",
                "email": "Asha@Example.com",
                "instagram": "asha.makes",
                "phone": "+91 98765 43210",
                "about": "Handmade jewellery",
            },
        )
        self.assertRedirects(response, reverse("access-requested"))
        self.assertFalse(email_is_registered("asha@example.com"))
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ["support@hookd.world"])
        self.assertEqual(message.reply_to, ["asha@example.com"])
        self.assertIn("@asha.makes", message.subject)
        self.assertIn("Handmade jewellery", message.body)
        self.assertIn("+91 98765 43210", message.body)

    def test_request_requires_details(self):
        response = self.client.post(reverse("signup"), {"email": "not-an-email"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "field__input--error")
        self.assertEqual(len(mail.outbox), 0)


@override_settings(SIGNUPS_OPEN=True)
class AuthPageTests(TestCase):
    def test_signup_creates_user_and_logs_in(self):
        response = self.client.post(
            reverse("signup"),
            {"name": "Asha", "email": "Creator@Example.com", "password": "a-strong-pass-123"},
        )
        # A new creator has no accounts yet, so the studio sends them on to connections.
        self.assertRedirects(response, reverse("studio"), target_status_code=302)
        self.assertTrue(email_is_registered("creator@example.com"))
        self.assertEqual(self.client.get(reverse("connections")).status_code, 200)

    def test_signup_shows_field_errors(self):
        create_user(email="creator@example.com", password="a-strong-pass-123")
        response = self.client.post(reverse("signup"), {"email": "creator@example.com", "password": "123"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")
        self.assertContains(response, "field__input--error")

    def test_login_rejects_bad_password(self):
        create_user(email="creator@example.com", password="a-strong-pass-123")
        response = self.client.post(reverse("login"), {"email": "creator@example.com", "password": "wrong"})
        self.assertContains(response, "Invalid email or password.")

    def test_login_returns_to_safe_next_only(self):
        create_user(email="creator@example.com", password="a-strong-pass-123")
        credentials = {"email": "creator@example.com", "password": "a-strong-pass-123"}
        response = self.client.post(reverse("login"), {**credentials, "next": "/auth/instagram/callback?code=1"})
        self.assertRedirects(response, "/auth/instagram/callback?code=1", fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(reverse("login"), {**credentials, "next": "https://evil.example.com/"})
        self.assertRedirects(response, reverse("studio"), target_status_code=302)

    def test_logout_requires_post_and_ends_session(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("logout")), reverse("home"))
        self.assertRedirects(
            self.client.get(reverse("connections")), f"{reverse('login')}?next={reverse('connections')}"
        )
