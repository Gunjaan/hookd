
from django.conf import settings
from django.core import mail
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.core import crypto


class HealthViewTests(TestCase):
    def test_reports_database_status(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "database": True})


class LandingViewTests(TestCase):
    def test_landing_has_link_preview_tags(self):
        response = self.client.get(reverse("home"), HTTP_HOST="localhost")
        self.assertContains(response, '<meta property="og:title" content="Hookd: your very own agent to get the world hookd" />')
        self.assertContains(response, '<meta property="og:url" content="http://localhost/" />')
        self.assertContains(response, '<meta property="og:image" content="http://localhost/static/img/og-image.png" />')
        self.assertContains(response, '<meta name="twitter:card" content="summary_large_image" />')
        self.assertContains(response, '<link rel="canonical" href="http://localhost/" />')
        self.assertContains(response, '<meta name="robots" content="index, follow" />')

    def test_signed_in_pages_are_not_indexed(self):
        user = create_user(email="meta@example.com", password="pass-12345")
        self.client.force_login(user)
        response = self.client.get(reverse("connections"))
        self.assertContains(response, '<meta name="robots" content="noindex, nofollow" />')

    def test_guest_sees_landing(self):
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your very own agent to get the world")
        self.assertContains(response, 'id="pipeline"')
        self.assertContains(response, 'id="learns"')
        self.assertContains(response, "Hooky")
        self.assertContains(response, 'id="faq"')
        self.assertContains(response, "data-highlights")
        self.assertContains(response, "data-highlight")
        self.assertContains(response, "js/landing.js")
        self.assertContains(response, 'id="try"')
        self.assertContains(response, "while.wanderingg")
        self.assertContains(response, "schedule_followup")
        self.assertContains(response, 'id="contact"')
        self.assertContains(response, reverse("api-contact"))

    def test_signed_in_user_sees_landing_with_studio_link(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(user)
        response = self.client.get(reverse("home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("studio"))


class ContactApiTests(TestCase):
    url = reverse("api-contact")
    details = {"name": "Asha", "email": "Asha@Example.com", "message": "Can I see Hooky's inbox?"}

    def setUp(self):
        cache.clear()

    def test_sends_one_email_to_support_with_reply_to(self):
        response = self.client.post(self.url, self.details)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Thank you, Asha.", response.json()["html"])
        self.assertEqual(len(mail.outbox), 1)
        email = mail.outbox[0]
        self.assertEqual(email.to, [settings.SUPPORT_EMAIL])
        self.assertEqual(email.reply_to, ["asha@example.com"])
        self.assertIn("Can I see Hooky's inbox?", email.body)

    def test_field_error(self):
        response = self.client.post(self.url, {**self.details, "email": "not an email"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Email", response.json()["error"])
        self.assertEqual(mail.outbox, [])

    def test_rate_limited_per_address(self):
        for _ in range(5):
            self.assertEqual(self.client.post(self.url, self.details).status_code, 200)
        response = self.client.post(self.url, self.details)
        self.assertEqual(response.status_code, 429)
        self.assertIn("try again in an hour", response.json()["error"])
        self.assertEqual(len(mail.outbox), 5)
        other = self.client.post(self.url, self.details, HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(other.status_code, 200)

    def test_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_csrf_enforced(self):
        response = Client(enforce_csrf_checks=True).post(self.url, self.details)
        self.assertEqual(response.status_code, 403)


class CryptoTests(SimpleTestCase):
    def tearDown(self):
        crypto._cipher.cache_clear()

    def test_round_trip_and_key_rotation(self):
        old_key, new_key = crypto.generate_key(), crypto.generate_key()
        with override_settings(FIELD_ENCRYPTION_KEYS=[old_key]):
            crypto._cipher.cache_clear()
            ciphertext = crypto.encrypt("secret")
        with override_settings(FIELD_ENCRYPTION_KEYS=[new_key, old_key]):
            crypto._cipher.cache_clear()
            self.assertEqual(crypto.decrypt(ciphertext), "secret")

    def test_unknown_key_raises(self):
        with override_settings(FIELD_ENCRYPTION_KEYS=[crypto.generate_key()]):
            crypto._cipher.cache_clear()
            ciphertext = crypto.encrypt("secret")
        with override_settings(FIELD_ENCRYPTION_KEYS=[crypto.generate_key()]):
            crypto._cipher.cache_clear()
            with self.assertRaises(crypto.DecryptionError):
                crypto.decrypt(ciphertext)
