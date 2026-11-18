import io
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, override_settings
from django.urls import reverse
from django.utils import timezone
from google.genai import types
from PIL import Image

from apps.accounts.model_querysets import create_user
from apps.content.models import PublishJob
from apps.content.tests import IG_ID, StudioTestCase, media
from apps.creatives import prompts, services
from apps.agent.models import AgentProfile, PostBrain
from apps.ai import services as ai_services
from apps.ai.models import AiCall
from apps.creatives.engines.base import (
    BrainQuestion,
    CopyEngine,
    ImageEngine,
    PostBrief,
    PostCopy,
    ProductPhoto,
    RenderedImage,
    ShotPlan,
)
from apps.creatives.engines.gemini import GeminiImageEngine
from apps.creatives.exceptions import CreativeEngineError
from apps.creatives.models import Creative, CreativeSlide, CreativeSlideVersion

PRICES = {"copy-model": {"input": 0.25, "output": 1.5}, "image-model": {"input": 0.5, "output": 60}}


def image_bytes(size=(2000, 1500), fmt="JPEG", color=(200, 30, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def photo(name="tee.jpg", **kwargs) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, image_bytes(**kwargs), content_type="image/jpeg")


def billed(model: str, input_tokens: int, output_tokens: int) -> None:
    """Stands in for the provider client's call record."""
    ai_services.record(provider="claude", model=model, request={}, input_tokens=input_tokens, output_tokens=output_tokens)


class FakeCopyEngine(CopyEngine):
    calls = []
    shots = 3
    fail = False
    blocked_reason = ""

    def plan(self, photos, request_text):
        FakeCopyEngine.calls.append(("plan", len(photos), request_text))
        if FakeCopyEngine.fail:
            raise CreativeEngineError("Planning broke.")
        billed("copy-model", 2000, 800)
        return PostBrief(
            product="black cotton t-shirt",
            branding=["white text 'SALT CO.' centered on chest"],
            audience="young men",
            slides=[ShotPlan(role=f"shot{i}", prompt=f"Shot {i} on a model") for i in range(FakeCopyEngine.shots)],
            caption="Meet the tee you will live in.",
            hashtags=["#TShirt", "streetwear", "tshirt", "salt co"],
            questions=[
                BrainQuestion(label="Price", question="What is the price?", scope="post", answer=""),
                BrainQuestion(label="Sizes", question="What sizes do you have?", scope="post", answer="S, M, L"),
                BrainQuestion(label="Delivery", question="How long does delivery take?", scope="account", answer=""),
            ],
            blocked_reason=FakeCopyEngine.blocked_reason,
        )

    def rewrite(self, request_text):
        FakeCopyEngine.calls.append(("rewrite", 0, request_text))
        billed("copy-model", 300, 100)
        return PostCopy(caption="Shorter caption.", hashtags=["cotton"])


class FakeImageEngine(ImageEngine):
    prompts = []
    fail_on = set()

    def render(self, photos, prompt):
        FakeImageEngine.prompts.append(prompt)
        if any(marker in prompt for marker in FakeImageEngine.fail_on):
            billed("image-model", 600, 0)
            raise CreativeEngineError("Blocked shot.")
        billed("image-model", 600, 1290)
        return RenderedImage(image_bytes(size=(1024, 1024), fmt="PNG"))


ENGINES = {
    "HOOKD_COPY_ENGINE": "apps.creatives.tests.FakeCopyEngine",
    "HOOKD_IMAGE_ENGINE": "apps.creatives.tests.FakeImageEngine",
    "ANTHROPIC_API_KEY": "test-key",
    "GEMINI_API_KEY": "test-key",
    "AI_MODEL_PRICES": PRICES,
}


@override_settings(**ENGINES)
class CreativeTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        FakeCopyEngine.calls = []
        FakeCopyEngine.shots = 3
        FakeCopyEngine.fail = False
        FakeCopyEngine.blocked_reason = ""
        FakeImageEngine.prompts = []
        FakeImageEngine.fail_on = set()

    def create(self, **data):
        payload = {"photos": [photo()], "look": "model", "format_choice": "carousel", "notes": "Diwali mood", **data}
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("creative-new"), payload)


class CreateTests(CreativeTestCase):
    def test_new_page_renders_steps(self):
        response = self.client.get(reverse("creative-new"))
        self.assertContains(response, "Add your product photos")
        self.assertContains(response, "Pick a look")
        self.assertContains(response, "Anything special?")

    def test_carousel_draft_is_planned_rendered_and_ready(self):
        response = self.create()
        creative = Creative.objects.get()
        self.assertRedirects(response, reverse("creative-detail", args=[creative.pk]), fetch_redirect_response=False)
        self.assertEqual(creative.status, "ready")
        self.assertEqual(creative.caption, "Meet the tee you will live in.")
        self.assertEqual(creative.hashtags, "#tshirt #streetwear #saltco")
        self.assertEqual(creative.brief["branding"], ["white text 'SALT CO.' centered on chest"])
        self.assertEqual(creative.slides.count(), 3)

        kind, photo_count, request_text = FakeCopyEngine.calls[0]
        self.assertEqual((kind, photo_count), ("plan", 1))
        self.assertIn("Look: model", request_text)
        self.assertIn("carousel (3 to 5 shots)", request_text)
        self.assertIn("Notes: Diwali mood", request_text)
        self.assertEqual(len(FakeCopyEngine.calls), 1)

        self.assertEqual(len(FakeImageEngine.prompts), 3)
        self.assertIn("Shot 0 on a model", FakeImageEngine.prompts[0])
        self.assertIn("white text 'SALT CO.' centered on chest", FakeImageEngine.prompts[0])
        slide = creative.slides.first()
        with Image.open(slide.image.open("rb")) as rendered:
            self.assertEqual((rendered.format, rendered.size), ("JPEG", (1080, 1350)))

        page = self.client.get(reverse("creative-detail", args=[creative.pk]))
        self.assertContains(page, "Your post is ready")
        self.assertContains(page, "Post now")
        self.assertContains(self.client.get(reverse("publishing")), "Ready to post")

    def test_sources_are_downscaled_jpegs(self):
        self.create(photos=[SimpleUploadedFile("tee.png", image_bytes(size=(3000, 2000), fmt="PNG"), content_type="image/png")])
        source = Creative.objects.get().sources.get()
        with Image.open(source.file.open("rb")) as stored:
            self.assertEqual(stored.format, "JPEG")
            self.assertEqual(max(stored.size), 1024)

    def test_single_keeps_one_slide(self):
        FakeCopyEngine.shots = 3
        self.create(format_choice="single")
        self.assertEqual(Creative.objects.get().slides.count(), 1)

    def test_one_failed_slide_still_ready(self):
        FakeImageEngine.fail_on = {"Shot 1"}
        self.create()
        creative = Creative.objects.get()
        self.assertEqual(creative.status, "ready")
        self.assertEqual(list(creative.slides.values_list("status", flat=True)), ["ready", "failed", "ready"])

    def test_all_slides_failing_marks_failed(self):
        FakeImageEngine.fail_on = {"Shot"}
        self.create()
        self.assertEqual(Creative.objects.get().status, "failed")

    def test_planning_error_marks_failed(self):
        FakeCopyEngine.fail = True
        self.create()
        creative = Creative.objects.get()
        self.assertEqual((creative.status, creative.error), ("failed", "Planning broke."))
        self.assertContains(self.client.get(reverse("creative-detail", args=[creative.pk])), "Planning broke.")

    def test_planner_refusal_stops_the_draft_before_any_render(self):
        FakeCopyEngine.blocked_reason = "Hookd cannot make posts for tobacco products."
        self.create()
        creative = Creative.objects.get()
        self.assertEqual((creative.status, creative.error), ("failed", "Hookd cannot make posts for tobacco products."))
        self.assertEqual(creative.slides.count(), 0)
        self.assertEqual(FakeImageEngine.prompts, [])

    def test_photo_validation(self):
        response = self.client.post(reverse("creative-new"), {"photos": [photo() for _ in range(4)], "look": "model", "format_choice": "auto"})
        self.assertContains(response, "Add 1 to 3 photos.")
        response = self.client.post(
            reverse("creative-new"),
            {"photos": [SimpleUploadedFile("tee.jpg", b"not an image")], "look": "model", "format_choice": "auto"},
        )
        self.assertContains(response, "not a readable photo")
        response = self.client.post(
            reverse("creative-new"),
            {"photos": [SimpleUploadedFile("tee.gif", b"GIF89a")], "look": "model", "format_choice": "auto"},
        )
        self.assertContains(response, "use a JPEG, PNG or WebP photo")
        self.assertFalse(Creative.objects.exists())

    def test_missing_keys_are_explained(self):
        for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
            with self.subTest(key), override_settings(**{key: ""}):
                self.assertContains(self.create(), f"{key} is missing")
        self.assertFalse(Creative.objects.exists())

    @override_settings(CREATIVE_DAILY_LIMIT=1)
    def test_daily_limit(self):
        self.create()
        response = self.create()
        self.assertContains(response, "today&#x27;s limit")
        self.assertEqual(Creative.objects.count(), 1)

    def test_stale_slide_is_failed_on_view(self):
        self.create()
        creative = Creative.objects.get()
        slide = creative.slides.first()
        CreativeSlide.objects.filter(pk=slide.pk).update(status="pending", updated_at=timezone.now() - timedelta(minutes=10))
        Creative.objects.filter(pk=creative.pk).update(status="generating")
        self.client.get(reverse("creative-detail", args=[creative.pk]))
        slide.refresh_from_db()
        creative.refresh_from_db()
        self.assertEqual((slide.status, creative.status), ("failed", "ready"))


class EditTests(CreativeTestCase):
    def setUp(self):
        super().setUp()
        self.create()
        self.creative = Creative.objects.get()
        FakeCopyEngine.calls = []
        FakeImageEngine.prompts = []

    def draft_form(self, **data):
        """What the draft page sends with Save draft and Rewrite: the edited copy and answers."""
        return {"caption": "My edited caption", "hashtags": "#mine", "answer_0": "Rs 799", "answer_2": "3 days", **data}

    def redo(self, slide, tweak=""):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(reverse("api-slide-redo", args=[slide.pk]), {"tweak": tweak})

    def test_redo_renders_only_that_slide_with_the_change(self):
        slide = self.creative.slides.last()
        self.redo(slide, "outdoor light")
        self.assertEqual(len(FakeImageEngine.prompts), 1)
        self.assertIn("Shot 2 on a model", FakeImageEngine.prompts[0])
        self.assertIn("Change: outdoor light", FakeImageEngine.prompts[0])
        self.assertEqual(FakeCopyEngine.calls, [])
        self.creative.refresh_from_db()
        self.assertEqual(self.creative.status, "ready")

    def test_redo_answers_with_the_working_draft_state(self):
        slide = self.creative.slides.first()
        data = self.client.post(reverse("api-slide-redo", args=[slide.pk]), {"tweak": "outdoor"}).json()  # task not run
        self.assertEqual((data["status"], data["working"], data["can_post"]), ("generating", True, False))
        self.assertEqual(data["alert"]["kind"], "info")
        first = data["slides"][0]
        self.assertEqual(first["id"], slide.pk)
        self.assertIn("Making photo 1", first["html"])
        self.assertNotIn("Redo this photo", first["html"])  # nothing can be redone while a photo renders
        self.assertIn("Earlier versions (1)", first["html"])

    def test_redo_does_not_touch_the_copy(self):
        self.client.post(reverse("api-draft-copy", args=[self.creative.pk]), self.draft_form())
        self.redo(self.creative.slides.first())
        self.creative.refresh_from_db()
        self.assertEqual((self.creative.caption, self.creative.hashtags), ("My edited caption", "#mine"))
        self.assertEqual([q["answer"] for q in self.creative.brain_questions], ["Rs 799", "S, M, L", "3 days"])

    def test_redo_while_rendering_is_refused(self):
        slides = list(self.creative.slides.all())
        self.client.post(reverse("api-slide-redo", args=[slides[0].pk]))  # task not run
        response = self.client.post(reverse("api-slide-redo", args=[slides[1].pk]))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "This draft is not ready yet.")

    def test_redo_keeps_the_old_photo_as_a_version(self):
        slide = self.creative.slides.first()
        old_name = slide.image.name
        self.redo(slide, "outdoor")
        slide.refresh_from_db()
        version = slide.versions.get()
        self.assertEqual((version.image.name, version.tweak), (old_name, ""))
        self.assertTrue(version.image.storage.exists(old_name))
        self.assertNotEqual(slide.image.name, old_name)
        self.assertContains(self.client.get(reverse("creative-detail", args=[self.creative.pk])), "Earlier versions (1)")

    def test_failed_redo_still_has_the_old_photo(self):
        slide = self.creative.slides.first()
        FakeImageEngine.fail_on = {"Change: blocked"}
        self.redo(slide, "blocked")
        slide.refresh_from_db()
        self.assertEqual((slide.status, slide.versions.count()), ("failed", 1))

    def test_use_earlier_version_swaps_back_without_ai(self):
        slide = self.creative.slides.first()
        first = slide.image.name
        self.redo(slide, "outdoor")
        slide.refresh_from_db()
        second = slide.image.name
        FakeImageEngine.prompts = []
        calls = AiCall.objects.count()
        data = self.client.post(reverse("api-version-use", args=[slide.versions.get().pk])).json()
        self.assertEqual((data["status"], data["can_post"], data["message"]), ("ready", True, "Earlier photo is back in the post."))
        slide.refresh_from_db()
        self.assertEqual(slide.image.name, first)
        self.assertEqual(list(slide.versions.values_list("image", "tweak")), [(second, "outdoor")])
        self.assertEqual((FakeImageEngine.prompts, AiCall.objects.count()), ([], calls))

    def test_rewrite_is_text_only_and_keeps_answers(self):
        data = self.client.post(reverse("api-draft-rewrite", args=[self.creative.pk]), self.draft_form(instruction="shorter")).json()
        kind, photo_count, request_text = FakeCopyEngine.calls[0]
        self.assertEqual((kind, photo_count), ("rewrite", 0))
        self.assertIn("Change: shorter", request_text)
        self.assertIn("SALT CO.", request_text)
        self.assertIn("My edited caption", request_text)
        self.assertEqual((data["caption"], data["hashtags"]), ("Shorter caption.", "#cotton"))
        self.assertEqual(data["full_caption"], "Shorter caption.\n\n#cotton")
        self.creative.refresh_from_db()
        self.assertEqual((self.creative.caption, self.creative.hashtags), ("Shorter caption.", "#cotton"))
        self.assertEqual(self.creative.brain_questions[0]["answer"], "Rs 799")

    def test_draft_page_shows_original_photos(self):
        source = self.creative.sources.get()
        page = self.client.get(reverse("creative-detail", args=[self.creative.pk]))
        self.assertContains(page, "Your photo")
        self.assertContains(page, reverse("creative-file", args=["source", source.pk]))
        self.assertContains(page, 'class="draft-story__step draft-story__step--active" aria-current="step"')
        self.assertContains(page, 'data-modal-images="' + reverse("creative-file", args=["source", source.pk]))
        self.assertContains(page, 'data-carousel-step="1"')
        self.assertContains(page, 'data-carousel-dot="2"')

    def test_draft_page_uses_the_json_endpoints(self):
        page = self.client.get(reverse("creative-detail", args=[self.creative.pk])).content.decode()
        self.assertIn(f'data-draft="{reverse("api-draft-progress", args=[self.creative.pk])}"', page)
        self.assertIn(reverse("api-draft-copy", args=[self.creative.pk]), page)
        self.assertIn(reverse("api-slide-redo", args=[self.creative.slides.first().pk]), page)
        self.assertNotIn('http-equiv="refresh"', page)

    def test_progress_reports_the_draft_state(self):
        data = self.client.get(reverse("api-draft-progress", args=[self.creative.pk])).json()
        self.assertEqual((data["status"], data["title"], data["working"]), ("ready", "Your post is ready", False))
        self.assertEqual(len(data["slides"]), 3)
        self.assertIn("Redo this photo", data["slides"][0]["html"])
        self.assertIn("Post when you are happy", data["story"])

    def test_progress_fails_a_stale_slide(self):
        slide = self.creative.slides.first()
        self.client.post(reverse("api-slide-redo", args=[slide.pk]))  # task not run
        CreativeSlide.objects.filter(pk=slide.pk).update(updated_at=timezone.now() - timedelta(minutes=6))
        data = self.client.get(reverse("api-draft-progress", args=[self.creative.pk])).json()
        self.assertEqual((data["status"], data["working"]), ("ready", False))
        self.assertIn("took too long", data["slides"][0]["html"])

    def test_save_copy_normalizes_hashtags(self):
        data = self.client.post(
            reverse("api-draft-copy", args=[self.creative.pk]), {"caption": " New caption ", "hashtags": "#a, b #A c-d"}
        ).json()
        self.assertEqual((data["caption"], data["hashtags"], data["message"]), ("New caption", "#a #b #cd", "Draft saved."))
        self.creative.refresh_from_db()
        self.assertEqual((self.creative.caption, self.creative.hashtags), ("New caption", "#a #b #cd"))

    def test_too_long_caption_is_refused(self):
        response = self.client.post(
            reverse("api-draft-copy", args=[self.creative.pk]), {"caption": "x" * 2195, "hashtags": "#longtag"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("under 2200 characters", response.json()["error"])

    def test_api_needs_login_and_csrf(self):
        url = reverse("api-draft-copy", args=[self.creative.pk])
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        self.assertEqual(strict.post(url, {"caption": "x"}).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(url, {"caption": "x"}).status_code, 401)

    def test_delete_removes_draft_and_files(self):
        slide = self.creative.slides.first()
        self.redo(slide)
        names = [CreativeSlideVersion.objects.get().image.name, slide.image.name, self.creative.sources.get().file.name]
        storage = slide.image.storage
        self.client.post(reverse("creative-delete", args=[self.creative.pk]))
        self.assertFalse(Creative.objects.exists())
        self.assertFalse(any(storage.exists(name) for name in names))

    def test_other_user_gets_404(self):
        slide = self.creative.slides.first()
        self.client.force_login(create_user(email="other@example.com", password="a-strong-pass-123"))
        self.assertEqual(self.client.get(reverse("creative-detail", args=[self.creative.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("creative-file", args=["slide", slide.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("api-slide-redo", args=[slide.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("api-draft-progress", args=[self.creative.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("api-draft-copy", args=[self.creative.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("creative-delete", args=[self.creative.pk])).status_code, 404)

    def test_other_user_cannot_see_or_use_versions(self):
        self.redo(self.creative.slides.first())
        version = CreativeSlideVersion.objects.get()
        self.client.force_login(create_user(email="other@example.com", password="a-strong-pass-123"))
        self.assertEqual(self.client.get(reverse("creative-file", args=["version", version.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("api-version-use", args=[version.pk])).status_code, 404)

    def test_owner_gets_images(self):
        slide = self.creative.slides.first()
        response = self.client.get(reverse("creative-file", args=["slide", slide.pk]))
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "image/jpeg"))
        download = self.client.get(reverse("creative-file", args=["slide", slide.pk]) + "?download=1")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(self.client.get(reverse("creative-file", args=["other", slide.pk])).status_code, 404)


class PublishCase(CreativeTestCase):
    def setUp(self):
        super().setUp()
        self.graph.on("POST", f"{IG_ID}/media", self._create_container)
        self.graph.on("GET", f"{IG_ID}/content_publishing_limit", {"data": [{"quota_usage": 1, "config": {"quota_total": 100}}]})
        self.graph.on("GET", "container1", {"status_code": "FINISHED"})
        self.graph.on("POST", f"{IG_ID}/media_publish", {"id": "m99"})
        self.graph.on("GET", "m99", media(99))
        self.containers = []

    def _create_container(self, data):
        self.containers.append(data)
        return {"id": "container1" if "caption" in data else f"child{len(self.containers)}"}

    def publish(self, creative, **data):
        payload = {"caption": "Edited caption", "hashtags": "#tee #cotton", "account": self.account.pk, **data}
        return self.client.post(reverse("creative-publish", args=[creative.pk]), payload)


class PublishTests(PublishCase):
    def test_carousel_publishes_with_edited_caption_and_hashtags(self):
        self.create()
        creative = Creative.objects.get()
        response = self.publish(creative)
        self.assertRedirects(response, reverse("publishing"), fetch_redirect_response=False)
        job = PublishJob.objects.get()
        self.assertEqual((job.kind, job.status), ("carousel", "published"))
        self.assertEqual(self.containers[-1]["caption"], "Edited caption\n\n#tee #cotton")
        self.assertEqual(self.containers[-1]["children"], "child1,child2,child3")
        creative.refresh_from_db()
        self.assertEqual((creative.status, creative.publish_job, creative.caption), ("published", job, "Edited caption"))

    def test_single_slide_publishes_as_image(self):
        self.create(format_choice="single")
        self.publish(Creative.objects.get())
        self.assertEqual(PublishJob.objects.get().kind, "image")

    def test_draft_still_rendering_cannot_publish(self):
        self.create()
        creative = Creative.objects.get()
        Creative.objects.filter(pk=creative.pk).update(status="generating")
        response = self.publish(creative, account=self.account.pk)
        self.assertRedirects(response, reverse("creative-detail", args=[creative.pk]), fetch_redirect_response=False)
        self.assertFalse(PublishJob.objects.exists())

    @override_settings(APP_URL="http://localhost:8000")
    def test_publishing_errors_are_shown(self):
        self.create()
        creative = Creative.objects.get()
        response = self.client.post(
            reverse("creative-publish", args=[creative.pk]),
            {"caption": "c", "hashtags": "", "account": self.account.pk},
            follow=True,
        )
        self.assertContains(response, "HTTPS")
        creative.refresh_from_db()
        self.assertEqual(creative.status, "ready")


class BrainQuestionTests(PublishCase):
    def test_questions_are_stored_and_shown(self):
        self.create()
        creative = Creative.objects.get()
        self.assertEqual([q["label"] for q in creative.brain_questions], ["Price", "Sizes", "Delivery"])
        page = self.client.get(reverse("creative-detail", args=[creative.pk]))
        self.assertContains(page, "Teach your reply agent")
        self.assertContains(page, 'value="S, M, L"')
        self.assertContains(page, 'form="draft-form"')

    def test_save_keeps_answers(self):
        self.create()
        creative = Creative.objects.get()
        self.client.post(
            reverse("api-draft-copy", args=[creative.pk]),
            {"caption": "c", "hashtags": "", "answer_0": " Rs 799 ", "answer_2": "3 to 5 days"},
        )
        creative.refresh_from_db()
        self.assertEqual([q["answer"] for q in creative.brain_questions], ["Rs 799", "S, M, L", "3 to 5 days"])

    def test_publishing_teaches_post_and_account_brains(self):
        AgentProfile.objects.create(account=self.account, knowledge="Delivery: 7 days\nCOD available")
        self.create()
        creative = Creative.objects.get()
        self.publish(creative, answer_0="Rs 799", answer_2="3 to 5 days")
        brain = PostBrain.objects.get(post__external_id="m99")
        self.assertEqual(brain.knowledge, "Price: Rs 799\nSizes: S, M, L")
        self.assertTrue(brain.agent_enabled)
        self.assertEqual(AgentProfile.objects.get(account=self.account).knowledge, "COD available\nDelivery: 3 to 5 days")
        creative.refresh_from_db()
        self.assertIsNotNone(creative.brain_saved_at)
        page = self.client.get(reverse("creative-detail", args=[creative.pk]))
        self.assertContains(page, "What Hooky <em>knows</em>")
        self.assertContains(page, "3 answers saved for replies")
        self.assertContains(page, "Published")
        post = PostBrain.objects.get().post
        self.assertContains(page, reverse("api-post-preview", args=[post.pk]))
        self.assertNotContains(page, "See status")

    def test_post_that_goes_live_later_is_taught_by_the_signal(self):
        from apps.content import services as content_services

        self.graph.on("GET", "container1", {"status_code": "IN_PROGRESS"})
        self.create()
        creative = Creative.objects.get()
        with mock.patch("apps.content.tasks.poll_publish_job.apply_async"):
            self.publish(creative, answer_0="Rs 799")
        self.assertFalse(PostBrain.objects.exists())
        self.graph.on("GET", "container1", {"status_code": "FINISHED"})
        content_services.advance_publish_job(PublishJob.objects.get())
        self.assertEqual(PostBrain.objects.get().knowledge, "Price: Rs 799\nSizes: S, M, L")

    def test_merge_replaces_same_label(self):
        self.assertEqual(services.merge_brain_lines("price: old\nReturns: 7 days", ["Price: Rs 799"]), "Returns: 7 days\nPrice: Rs 799")


class PromptTests(CreativeTestCase):
    def test_plan_schema_asks_for_questions(self):
        self.assertIn("questions", prompts.PLAN_SCHEMA["required"])
        self.assertIn("REPLY AGENT QUESTIONS", prompts.PLAN_SYSTEM)

    def test_system_prompts_hold_no_per_request_values(self):
        self.assertNotIn("{", prompts.PLAN_SYSTEM)
        self.assertNotIn("{", prompts.REWRITE_SYSTEM)

    def test_plan_schema_asks_for_blocked_reason(self):
        self.assertIn("blocked_reason", prompts.PLAN_SCHEMA["required"])
        self.assertIn("SAFETY", prompts.PLAN_SYSTEM)

    def test_image_prompt_holds_the_safety_rules(self):
        text = prompts.image_prompt(prompt="On a model", branding=[], tweak="")
        self.assertIn("adults only, fully clothed", text)
        self.assertIn("No children, no real or famous people", text)

    def test_image_prompt_lists_branding(self):
        text = prompts.image_prompt(prompt="On a model", branding=["red label"], tweak="")
        self.assertIn("identical to the reference: red label", text)
        self.assertNotIn("Change:", text)

    def test_format_hashtags_caps_at_thirty(self):
        self.assertEqual(len(services.format_hashtags([f"tag{i}" for i in range(40)]).split()), 30)


class UsageTests(CreativeTestCase):
    def test_every_ai_call_is_recorded_with_its_cost(self):
        self.create()
        creative = Creative.objects.get()
        calls = list(AiCall.objects.order_by("pk"))
        self.assertEqual(
            [call.purpose for call in calls], ["creative_plan", "creative_photo", "creative_photo", "creative_photo"]
        )
        self.assertEqual({(call.user, call.subject) for call in calls}, {(self.user, creative)})
        plan = calls[0]
        self.assertEqual((plan.model, plan.input_tokens, plan.output_tokens), ("copy-model", 2000, 800))
        self.assertEqual(plan.cost_usd, Decimal("0.001700"))  # 2000 * 0.25 + 800 * 1.5 per million
        self.assertEqual(calls[1].cost_usd, Decimal("0.077700"))  # 600 * 0.5 + 1290 * 60 per million

        self.client.post(reverse("api-draft-rewrite", args=[creative.pk]), {"instruction": "shorter"})
        self.assertEqual(AiCall.objects.filter(purpose="caption_rewrite", subject_id=creative.pk).count(), 1)

    def test_failed_render_with_usage_is_recorded(self):
        FakeImageEngine.fail_on = {"Shot 1"}
        self.create()
        failed = AiCall.objects.get(purpose="creative_photo", output_tokens=0)
        self.assertEqual((failed.input_tokens, failed.output_tokens, failed.cost_usd), (600, 0, Decimal("0.000300")))

    @override_settings(AI_MODEL_PRICES={})
    def test_unknown_model_costs_zero(self):
        self.create()
        self.assertEqual(AiCall.objects.filter(cost_usd__gt=0).count(), 0)
        self.assertEqual(AiCall.objects.count(), 4)

    def test_admin_shows_usage_totals(self):
        self.create()
        self.client.force_login(get_user_model().objects.create_superuser(email="admin@example.com", password="a-strong-pass-123"))
        listing = self.client.get(reverse("admin:creatives_creative_changelist"))
        self.assertContains(listing, "0.2348")  # 0.0017 + 3 * 0.0777
        calls = self.client.get(reverse("admin:ai_aicall_changelist"))
        self.assertContains(calls, "Filtered calls: 3800 input tokens, 4670 output tokens, 0.2348 USD")


class ClaudeCopyEngineTests(CreativeTestCase):
    @override_settings(CREATIVE_COPY_MODEL="cheap-model")
    def test_plan_sends_photos_and_caps_the_answer(self):
        from apps.creatives.engines.claude import ClaudeCopyEngine

        answer = {
            "product": "Tee",
            "branding": ["logo"],
            "audience": "students",
            "slides": [{"role": "hero", "prompt": f"Shot {n}"} for n in range(7)] + [{"role": "x", "prompt": " "}],
            "caption": "New tee",
            "hashtags": [f"tag{n}" for n in range(20)],
            "questions": [{"label": "Price", "question": f"Q{n}?", "scope": "odd", "answer": ""} for n in range(8)],
        }
        with mock.patch("apps.creatives.engines.claude.generate_json", return_value=answer) as call:
            brief = ClaudeCopyEngine().plan([ProductPhoto(b"jpeg-bytes")], "Look: model")
        self.assertEqual((len(brief.slides), len(brief.hashtags), len(brief.questions)), (5, 15, 6))
        self.assertEqual(brief.questions[0].scope, "post")
        model, content = call.call_args.args
        self.assertEqual(model, "cheap-model")
        self.assertEqual([block["type"] for block in content], ["image", "text"])
        self.assertEqual(content[0]["source"]["media_type"], "image/jpeg")


@override_settings(CREATIVE_IMAGE_MODEL="image-model")
class GeminiImageEngineTests(CreativeTestCase):
    def render(self, response):
        with mock.patch("apps.creatives.engines.gemini.generate", return_value=response) as self.call:
            return GeminiImageEngine().render([ProductPhoto(b"jpeg-bytes")], "On a model")

    def test_sends_strict_safety_settings(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[
            types.Part.from_bytes(data=b"png", mime_type="image/png"),
        ]))])
        self.assertEqual(self.render(response).data, b"png")
        self.assertEqual(
            {(item.category.name, item.threshold.name) for item in self.call.call_args.args[2].safety_settings},
            {
                ("HARM_CATEGORY_SEXUALLY_EXPLICIT", "BLOCK_LOW_AND_ABOVE"),
                ("HARM_CATEGORY_DANGEROUS_CONTENT", "BLOCK_LOW_AND_ABOVE"),
                ("HARM_CATEGORY_HARASSMENT", "BLOCK_LOW_AND_ABOVE"),
                ("HARM_CATEGORY_HATE_SPEECH", "BLOCK_LOW_AND_ABOVE"),
            },
        )

    def test_blocked_prompt_gives_the_safety_message(self):
        response = types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason.SAFETY),
        )
        with self.assertRaisesMessage(CreativeEngineError, "blocked by the safety filter"):
            self.render(response)

    def test_blocked_image_gives_the_safety_message(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.IMAGE_SAFETY)])
        with self.assertRaisesMessage(CreativeEngineError, "blocked by the safety filter"):
            self.render(response)

    def test_empty_answer_without_a_block_asks_to_retry(self):
        response = types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.STOP)])
        with self.assertRaisesMessage(CreativeEngineError, "did not return a photo"):
            self.render(response)
