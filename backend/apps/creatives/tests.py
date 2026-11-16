import io
from datetime import timedelta
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.model_querysets import create_user
from apps.content.models import PublishJob
from apps.content.tests import IG_ID, StudioTestCase, media
from apps.creatives import prompts, services
from apps.agent.models import AgentProfile, PostBrain
from apps.creatives.engines.base import BrainQuestion, CopyEngine, ImageEngine, PostBrief, PostCopy, ShotPlan
from apps.creatives.exceptions import CreativeEngineError
from apps.creatives.models import Creative, CreativeSlide


def image_bytes(size=(2000, 1500), fmt="JPEG", color=(200, 30, 90)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format=fmt)
    return buffer.getvalue()


def photo(name="tee.jpg", **kwargs) -> SimpleUploadedFile:
    return SimpleUploadedFile(name, image_bytes(**kwargs), content_type="image/jpeg")


class FakeCopyEngine(CopyEngine):
    calls = []
    shots = 3
    fail = False

    def plan(self, photos, request_text):
        FakeCopyEngine.calls.append(("plan", len(photos), request_text))
        if FakeCopyEngine.fail:
            raise CreativeEngineError("Planning broke.")
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
        )

    def rewrite(self, request_text):
        FakeCopyEngine.calls.append(("rewrite", 0, request_text))
        return PostCopy(caption="Shorter caption.", hashtags=["cotton"])


class FakeImageEngine(ImageEngine):
    prompts = []
    fail_on = set()

    def render(self, photos, prompt):
        FakeImageEngine.prompts.append(prompt)
        if any(marker in prompt for marker in FakeImageEngine.fail_on):
            raise CreativeEngineError("Blocked shot.")
        return image_bytes(size=(1024, 1024), fmt="PNG")


ENGINES = {
    "HOOKD_COPY_ENGINE": "apps.creatives.tests.FakeCopyEngine",
    "HOOKD_IMAGE_ENGINE": "apps.creatives.tests.FakeImageEngine",
    "GEMINI_API_KEY": "test-key",
}


@override_settings(**ENGINES)
class CreativeTestCase(StudioTestCase):
    def setUp(self):
        super().setUp()
        FakeCopyEngine.calls = []
        FakeCopyEngine.shots = 3
        FakeCopyEngine.fail = False
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

    @override_settings(GEMINI_API_KEY="")
    def test_missing_key_is_explained(self):
        response = self.create()
        self.assertContains(response, "GEMINI_API_KEY is missing")
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

    def test_redo_renders_only_that_slide_with_the_change(self):
        slide = self.creative.slides.last()
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("creative-slide-redo", args=[slide.pk]), {"tweak": "outdoor light"})
        self.assertEqual(len(FakeImageEngine.prompts), 1)
        self.assertIn("Shot 2 on a model", FakeImageEngine.prompts[0])
        self.assertIn("Change: outdoor light", FakeImageEngine.prompts[0])
        self.assertEqual(FakeCopyEngine.calls, [])
        self.creative.refresh_from_db()
        self.assertEqual(self.creative.status, "ready")

    def test_rewrite_is_text_only(self):
        self.client.post(reverse("creative-rewrite", args=[self.creative.pk]), {"instruction": "shorter"})
        kind, photo_count, request_text = FakeCopyEngine.calls[0]
        self.assertEqual((kind, photo_count), ("rewrite", 0))
        self.assertIn("Change: shorter", request_text)
        self.assertIn("SALT CO.", request_text)
        self.creative.refresh_from_db()
        self.assertEqual((self.creative.caption, self.creative.hashtags), ("Shorter caption.", "#cotton"))

    def test_save_copy_normalizes_hashtags(self):
        self.client.post(
            reverse("creative-copy", args=[self.creative.pk]), {"caption": " New caption ", "hashtags": "#a, b #A c-d"}
        )
        self.creative.refresh_from_db()
        self.assertEqual((self.creative.caption, self.creative.hashtags), ("New caption", "#a #b #cd"))

    def test_too_long_caption_is_refused(self):
        response = self.client.post(
            reverse("creative-copy", args=[self.creative.pk]), {"caption": "x" * 2195, "hashtags": "#longtag"}, follow=True
        )
        self.assertContains(response, "under 2200 characters")

    def test_delete_removes_draft_and_files(self):
        name = self.creative.slides.first().image.name
        storage = self.creative.slides.first().image.storage
        self.client.post(reverse("creative-delete", args=[self.creative.pk]))
        self.assertFalse(Creative.objects.exists())
        self.assertFalse(storage.exists(name))

    def test_other_user_gets_404(self):
        slide = self.creative.slides.first()
        self.client.force_login(create_user(email="other@example.com", password="a-strong-pass-123"))
        self.assertEqual(self.client.get(reverse("creative-detail", args=[self.creative.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("creative-file", args=["slide", slide.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("creative-slide-redo", args=[slide.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("creative-delete", args=[self.creative.pk])).status_code, 404)

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
            reverse("creative-copy", args=[creative.pk]),
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
        self.assertContains(page, "Your reply agent knows")

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

    def test_image_prompt_lists_branding(self):
        text = prompts.image_prompt(prompt="On a model", branding=["red label"], tweak="")
        self.assertIn("identical to the reference: red label", text)
        self.assertNotIn("Change:", text)

    def test_format_hashtags_caps_at_thirty(self):
        self.assertEqual(len(services.format_hashtags([f"tag{i}" for i in range(40)]).split()), 30)
