import io
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

import requests
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.model_querysets import create_user
from apps.comments.models import Comment
from apps.content import services
from apps.content.models import Post, PublishJob
from apps.content.tasks import poll_publish_job
from apps.content.templatetags.content_ui import metric_display
from apps.integrations import model_querysets as account_queries
from apps.integrations.providers.types import AccountProfile, TokenBundle

GRAPH = "https://graph.facebook.com/v27.0/"
IG_ID = "17841400000000"
INSTAGRAM_TEST_SETTINGS = {
    "APP_ID": "123",
    "APP_SECRET": "secret",
    "LOGIN_CONFIG_ID": "456",
    "REDIRECT_URI": "https://hookd.test/auth/instagram/callback",
    "GRAPH_API_VERSION": "v27.0",
}
MEDIA_ROOT = tempfile.mkdtemp(prefix="hookd-test-media-")


def media(index: int, **extra) -> dict:
    return {
        "id": f"m{index}",
        "caption": f"Post {index}",
        "media_type": "IMAGE",
        "media_product_type": "FEED",
        "media_url": f"https://cdn.test/m{index}.jpg",
        "permalink": f"https://instagram.com/p/m{index}",
        "timestamp": f"2026-11-{20 - index:02d}T10:00:00+0000",
        "like_count": index * 10,
        "comments_count": index,
        **extra,
    }


class FakeGraph:
    """Routes Graph API calls to canned responses and records them."""

    def __init__(self, test):
        self.test = test
        self.calls = []
        self.routes = {}

    def on(self, method: str, path: str, body, status: int = 200):
        self.routes[(method, path)] = (body, status)

    def __call__(self, method, url, params=None, data=None, timeout=None):
        self.test.assertTrue(url.startswith(GRAPH), url)
        path = url.removeprefix(GRAPH)
        self.calls.append((method, path, params or data or {}))
        if (method, path) not in self.routes:
            raise AssertionError(f"Unexpected request {method} {path}")
        body, status = self.routes[(method, path)]
        body = body(params or data or {}) if callable(body) else body
        response = mock.Mock(ok=status < 400, status_code=status)
        response.json.return_value = body
        return response

    def count(self, method: str, path: str) -> int:
        return sum(1 for call in self.calls if call[0] == method and call[1] == path)


@override_settings(INSTAGRAM=INSTAGRAM_TEST_SETTINGS, MEDIA_ROOT=MEDIA_ROOT, APP_URL="https://hookd.test")
class StudioTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def setUp(self):
        cache.clear()
        self.user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(self.user)
        self.account = account_queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id=IG_ID, username="asha.edits", metadata={"followers_count": 1200}),
            tokens=TokenBundle(access_token="suat"),
        )
        self.graph = FakeGraph(self)
        self.graph.on("GET", f"{IG_ID}/insights", {"data": [{"name": "reach", "total_value": {"value": 4321}}]})
        patcher = mock.patch.object(requests.Session, "request", side_effect=self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_post(self, **fields) -> Post:
        defaults = {"account": self.account, "external_id": "m1", "caption": "Hello", "posted_at": timezone.now()}
        return Post.objects.create(**{**defaults, **fields})


class DashboardTests(StudioTestCase):
    def test_studio_redirects_to_first_account(self):
        self.assertRedirects(
            self.client.get(reverse("studio")), reverse("studio-account", args=[self.account.pk]), fetch_redirect_response=False
        )

    def test_studio_without_accounts_goes_to_connections(self):
        account_queries.delete_account(self.account)
        response = self.client.get(reverse("studio"), follow=True)
        self.assertRedirects(response, reverse("connections"))
        self.assertContains(response, "Connect an account to open the studio.")

    def test_dashboard_shows_ten_recent_posts_and_caches_them(self):
        self.graph.on(
            "GET",
            f"{IG_ID}/media",
            {"data": [media(i) for i in range(1, 11)], "paging": {"cursors": {"after": "CUR1"}, "next": "https://next"}},
        )
        url = reverse("studio-account", args=[self.account.pk])
        response = self.client.get(url)
        self.assertContains(response, "@asha.edits")
        self.assertContains(response, "Post 10")
        self.assertContains(response, "100 likes")
        self.assertContains(response, "4,321")
        self.assertContains(response, "?after=CUR1")
        params = self.graph.calls[0][2]
        self.assertEqual(params["limit"], 10)
        self.assertNotIn("after", params)
        self.assertEqual(Post.objects.filter(account=self.account).count(), 10)

        self.client.get(url)
        self.assertEqual(self.graph.count("GET", f"{IG_ID}/media"), 1)
        self.assertEqual(self.graph.count("GET", f"{IG_ID}/insights"), 1)

    def test_last_page_has_no_load_more_link(self):
        self.graph.on("GET", f"{IG_ID}/media", {"data": [media(1)], "paging": {"cursors": {"after": "END"}}})
        response = self.client.get(reverse("studio-account", args=[self.account.pk]))
        self.assertNotContains(response, "Load older posts")

    def test_older_posts_use_the_cursor(self):
        self.graph.on("GET", f"{IG_ID}/media", lambda params: {"data": [media(11)] if params.get("after") == "CUR1" else []})
        response = self.client.get(reverse("studio-account", args=[self.account.pk]), {"after": "CUR1"})
        self.assertContains(response, "Older posts")
        self.assertContains(response, "Post 11")
        self.assertEqual(self.graph.count("GET", f"{IG_ID}/insights"), 0)

    def test_sync_removes_posts_deleted_on_the_platform(self):
        self.make_post(external_id="gone", posted_at=timezone.now())
        self.make_post(external_id="old", posted_at=timezone.now() - timedelta(days=400))
        self.graph.on("GET", f"{IG_ID}/media", {"data": [media(1, timestamp="2026-01-01T00:00:00+0000")]})
        self.client.get(reverse("studio-account", args=[self.account.pk]))
        self.assertEqual(sorted(Post.objects.values_list("external_id", flat=True)), ["m1", "old"])

    def test_refresh_bypasses_the_cache(self):
        self.graph.on("GET", f"{IG_ID}/media", {"data": [media(1)]})
        url = reverse("studio-account", args=[self.account.pk])
        self.client.get(url)
        response = self.client.post(reverse("studio-refresh", args=[self.account.pk]))
        self.assertRedirects(response, url, fetch_redirect_response=False)
        self.assertEqual(self.graph.count("GET", f"{IG_ID}/media"), 2)

    def test_expired_token_marks_account_and_shows_error(self):
        self.graph.on("GET", f"{IG_ID}/media", {"error": {"message": "Session expired", "code": 190}}, status=400)
        response = self.client.get(reverse("studio-account", args=[self.account.pk]))
        self.assertContains(response, "Session expired")
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, "expired")

    def test_other_users_account_is_404(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("studio-account", args=[self.account.pk])).status_code, 404)


class EnsurePostTests(StudioTestCase):
    def test_unknown_post_is_fetched_stored_and_clears_the_recent_posts_cache(self):
        self.graph.on("GET", "m7", media(7, caption="Made in the app"))
        cache.set(f"content:recent-posts:{self.account.pk}", {"ids": [], "next": ""})
        post = services.ensure_post(self.account, "m7")
        self.assertEqual(post.caption, "Made in the app")
        self.assertTrue(Post.objects.filter(account=self.account, external_id="m7").exists())
        self.assertIsNone(cache.get(f"content:recent-posts:{self.account.pk}"))

    def test_known_post_makes_no_call(self):
        post = self.make_post()
        self.assertEqual(services.ensure_post(self.account, "m1"), post)
        self.assertEqual(self.graph.calls, [])

    def test_empty_id_returns_none(self):
        self.assertIsNone(services.ensure_post(self.account, ""))
        self.assertEqual(self.graph.calls, [])

    def test_graph_error_returns_none(self):
        self.graph.on("GET", "m7", {"error": {"message": "Unsupported get request", "code": 100}}, status=400)
        self.assertIsNone(services.ensure_post(self.account, "m7"))
        self.assertFalse(Post.objects.filter(external_id="m7").exists())

    def test_expired_token_marks_the_account_and_returns_none(self):
        self.graph.on("GET", "m7", {"error": {"message": "Session expired", "code": 190}}, status=400)
        self.assertIsNone(services.ensure_post(self.account, "m7"))
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, self.account.Status.EXPIRED)


class PostDetailTests(StudioTestCase):
    def test_insights_are_fetched_once_and_cached(self):
        post = self.make_post()
        self.graph.on(
            "GET",
            "m1/insights",
            {"data": [{"name": "reach", "values": [{"value": 900}]}, {"name": "saved", "values": [{"value": 12}]}]},
        )
        url = reverse("post-detail", args=[post.pk])
        response = self.client.get(url)
        self.assertContains(response, "Reach")
        self.assertContains(response, "900")
        self.assertContains(response, "Saves")
        self.assertIn("saved", self.graph.calls[0][2]["metric"])
        self.client.get(url)
        self.assertEqual(self.graph.count("GET", "m1/insights"), 1)

        self.client.post(reverse("post-insights-refresh", args=[post.pk]))
        self.assertEqual(self.graph.count("GET", "m1/insights"), 2)

    def test_reel_insights_include_watch_time(self):
        post = self.make_post(product_type="reel", media_type="video")
        self.graph.on("GET", "m1/insights", {"data": [{"name": "ig_reels_avg_watch_time", "values": [{"value": 6500}]}]})
        response = self.client.get(reverse("post-detail", args=[post.pk]))
        self.assertIn("ig_reels_avg_watch_time", self.graph.calls[0][2]["metric"])
        self.assertContains(response, "Avg watch time")
        self.assertContains(response, "6.5s")

    def test_insights_error_still_renders_the_post(self):
        post = self.make_post()
        self.graph.on("GET", "m1/insights", {"error": {"message": "Not enough data", "code": 100}}, status=400)
        response = self.client.get(reverse("post-detail", args=[post.pk]))
        self.assertContains(response, "Insights unavailable: Not enough data")
        self.assertContains(response, "Hello")

    def test_post_page_does_not_load_comments(self):
        post = self.make_post(comments_count=40, insights_fetched_at=timezone.now())
        response = self.client.get(reverse("post-detail", args=[post.pk]))
        self.assertContains(response, "Show comments")
        self.assertEqual(self.graph.calls, [])

    def test_comments_load_one_page_and_are_stored(self):
        post = self.make_post()
        self.graph.on(
            "GET",
            "m1/comments",
            {
                "data": [
                    {"id": "c1", "text": "Love it", "timestamp": "2026-11-10T10:00:00+0000", "from": {"id": "9", "username": "fan"}},
                    {"id": "c2", "text": "Thanks!", "username": "asha.edits", "from": {"id": IG_ID, "username": "asha.edits"}},
                ],
                "paging": {"cursors": {"after": "C2"}, "next": "https://next"},
            },
        )
        response = self.client.get(reverse("post-comments", args=[post.pk]))
        self.assertContains(response, "@fan")
        self.assertContains(response, "Love it")
        self.assertContains(response, "?after=C2")
        self.assertEqual(self.graph.calls[0][2]["limit"], 25)
        self.assertEqual(Comment.objects.filter(account=self.account).count(), 2)
        self.assertTrue(Comment.objects.get(external_id="c2").is_from_account)

        self.client.get(reverse("post-comments", args=[post.pk]))
        self.assertEqual(Comment.objects.filter(account=self.account).count(), 2)

    def test_other_users_post_is_404(self):
        post = self.make_post()
        self.client.force_login(create_user(email="other@example.com", password="a-strong-pass-123"))
        self.assertEqual(self.client.get(reverse("post-detail", args=[post.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("post-comments", args=[post.pk])).status_code, 404)


def jpeg(name="photo.jpg") -> SimpleUploadedFile:
    return SimpleUploadedFile(name, b"\xff\xd8\xff fake jpeg", content_type="image/jpeg")


class PublishingTests(StudioTestCase):
    def setUp(self):
        super().setUp()
        self.graph.on("GET", f"{IG_ID}/content_publishing_limit", {"data": [{"quota_usage": 3, "config": {"quota_total": 100}}]})
        self.graph.on("POST", f"{IG_ID}/media", self._create_container)
        self.graph.on("GET", "container1", {"status_code": "FINISHED"})
        self.graph.on("POST", f"{IG_ID}/media_publish", {"id": "m99"})
        self.graph.on("GET", "m99", media(99, caption="My caption"))
        self.containers = []

    def _create_container(self, data):
        self.containers.append(data)
        return {"id": "container1" if "caption" in data else f"child{len(self.containers)}"}

    def compose(self, **data):
        return self.client.post(reverse("compose", args=[self.account.pk]), {"kind": "image", "caption": "My caption", **data})

    def test_compose_page_shows_quota(self):
        self.assertContains(self.client.get(reverse("compose", args=[self.account.pk])), "97 posts left")

    def test_image_publishes_right_away(self):
        response = self.compose(media=jpeg())
        self.assertRedirects(response, reverse("publishing"), fetch_redirect_response=False)
        job = PublishJob.objects.get()
        self.assertEqual(job.status, "published")
        self.assertEqual(job.post.external_id, "m99")
        self.assertEqual(self.containers[0]["caption"], "My caption")
        self.assertTrue(self.containers[0]["image_url"].startswith("https://hookd.test/publish-media/"))
        self.assertFalse(job.assets.exists())
        self.assertContains(self.client.get(reverse("publishing")), "Published")

    def test_carousel_creates_children_then_parent(self):
        self.compose(kind="carousel", media=[jpeg("a.jpg"), jpeg("b.jpg")])
        self.assertEqual([c.get("is_carousel_item") for c in self.containers[:2]], ["true", "true"])
        self.assertEqual(self.containers[2]["media_type"], "CAROUSEL")
        self.assertEqual(self.containers[2]["children"], "child1,child2")
        self.assertEqual(PublishJob.objects.get().status, "published")

    def test_reel_waits_for_processing_then_celery_publishes(self):
        self.graph.on("GET", "container1", {"status_code": "IN_PROGRESS"})
        with mock.patch.object(poll_publish_job, "apply_async") as queued, self.captureOnCommitCallbacks(execute=True):
            self.compose(kind="reel", media=SimpleUploadedFile("clip.mp4", b"video", content_type="video/mp4"))
        job = PublishJob.objects.get()
        queued.assert_called_once_with(args=[job.pk], countdown=20.0)
        self.assertEqual(job.status, "processing")
        self.assertEqual(self.containers[0]["media_type"], "REELS")
        asset = job.assets.get()
        served = self.client.get(reverse("publish-asset", args=[asset.token]))
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served["Content-Type"], "video/mp4")

        # Still processing: the task queues the next check.
        with mock.patch.object(poll_publish_job, "apply_async") as queued, self.captureOnCommitCallbacks(execute=True):
            poll_publish_job(job.pk)
        queued.assert_called_once()

        self.graph.on("GET", "container1", {"status_code": "FINISHED"})
        with mock.patch.object(poll_publish_job, "apply_async") as queued, self.captureOnCommitCallbacks(execute=True):
            poll_publish_job(job.pk)
        queued.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, "published")
        self.client.logout()
        self.assertEqual(self.client.get(reverse("publish-asset", args=[asset.token])).status_code, 404)

    def test_container_error_fails_the_job(self):
        self.graph.on("GET", "container1", {"status_code": "ERROR", "status": "Unsupported aspect ratio"})
        response = self.compose(media=jpeg())
        job = PublishJob.objects.get()
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "Unsupported aspect ratio")
        self.assertContains(self.client.get(response["Location"]), "Unsupported aspect ratio")

    def test_platform_rejecting_upload_fails_the_job(self):
        self.graph.on("POST", f"{IG_ID}/media", {"error": {"message": "Bad image", "code": 9004}}, status=400)
        self.compose(media=jpeg())
        self.assertEqual(PublishJob.objects.get().error, "Bad image")

    @override_settings(APP_URL="http://localhost:8000")
    def test_publishing_needs_public_https_url(self):
        response = self.compose(media=jpeg())
        self.assertContains(response, "public HTTPS address")
        self.assertFalse(PublishJob.objects.exists())

    def test_quota_reached(self):
        self.graph.on("GET", f"{IG_ID}/content_publishing_limit", {"data": [{"quota_usage": 100, "config": {"quota_total": 100}}]})
        response = self.compose(media=jpeg())
        self.assertContains(response, "publishing limit")
        self.assertFalse(PublishJob.objects.exists())

    def test_form_validation(self):
        self.assertContains(self.compose(kind="carousel", media=jpeg()), "A carousel needs 2 to 10 images.")
        self.assertContains(self.compose(media=jpeg("photo.png")), "images must be JPG or JPEG.")
        self.assertContains(self.compose(media=[jpeg("a.jpg"), jpeg("b.jpg")]), "Choose exactly one file.")
        self.assertFalse(PublishJob.objects.exists())

    def test_recovery_command_advances_processing_jobs(self):
        PublishJob.objects.create(account=self.account, kind="reel", publish_id="container1")
        out = io.StringIO()
        call_command("process_publish_jobs", stdout=out)
        self.assertIn("Published 1", out.getvalue())

    def test_check_status_only_for_own_jobs(self):
        job = PublishJob.objects.create(account=self.account, kind="reel", publish_id="container1")
        self.client.force_login(create_user(email="other@example.com", password="a-strong-pass-123"))
        self.assertEqual(self.client.post(reverse("publish-check", args=[job.pk])).status_code, 404)

    def test_stuck_job_times_out(self):
        self.graph.on("GET", "container1", {"status_code": "IN_PROGRESS"})
        job = PublishJob.objects.create(account=self.account, kind="reel", publish_id="container1")
        PublishJob.objects.filter(pk=job.pk).update(created_at=timezone.now() - timedelta(hours=2))
        self.client.post(reverse("publish-check", args=[job.pk]))
        job.refresh_from_db()
        self.assertEqual(job.status, "failed")


class MetricDisplayTests(TestCase):
    def test_formats(self):
        self.assertEqual(metric_display({"unit": "count", "value": 12345}), "12,345")
        self.assertEqual(metric_display({"unit": "milliseconds", "value": 6500}), "6.5s")
        self.assertEqual(metric_display({"unit": "milliseconds", "value": 125000}), "2m 5s")
        self.assertEqual(metric_display({"unit": "milliseconds", "value": 3_780_000}), "1h 3m")
