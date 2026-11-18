import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.model_querysets import create_user
from apps.agent import services
from apps.agent.constants import ReplyStatus
from apps.agent.engines.base import AgentDecision
from apps.agent.models import AgentProfile, CommentReply, LearnedFact, PostBrain
from apps.comments.models import Comment
from apps.content.models import Post
from apps.content.tests import FakeGraph
from apps.core.ai import JsonAnswer, TokenUsage
from apps.integrations import model_querysets as account_queries
from apps.integrations.models import SocialAccount
from apps.integrations.providers.instagram.provider import InstagramProvider
from apps.integrations.providers.types import AccountProfile, TokenBundle

IG_ID = "17841400000000"
INSTAGRAM_TEST_SETTINGS = {
    "APP_ID": "123",
    "APP_SECRET": "secret",
    "LOGIN_CONFIG_ID": "456",
    "REDIRECT_URI": "https://hookd.test/auth/instagram/callback",
    "GRAPH_API_VERSION": "v27.0",
    "WEBHOOK_VERIFY_TOKEN": "verify-me",
}


@override_settings(INSTAGRAM=INSTAGRAM_TEST_SETTINGS)
class AgentTestCase(TestCase):
    def setUp(self):
        self.user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.client.force_login(self.user)
        self.account = account_queries.upsert_account(
            user=self.user,
            platform="instagram",
            profile=AccountProfile(external_id=IG_ID, username="asha.store"),
            tokens=TokenBundle(access_token="suat"),
        )
        self.profile = AgentProfile.objects.create(
            account=self.account, enabled=True, knowledge="Free shipping above Rs 999\nReturns within 7 days"
        )
        self.post = Post.objects.create(account=self.account, external_id="m1", caption="New kurta drop", posted_at=timezone.now())
        self.graph = FakeGraph(self)
        patcher = mock.patch.object(requests.Session, "request", side_effect=self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.counter = 0

    def comment(self, text="Love this", *, author="fan1", media="m1", parent="", age=timedelta(0), account=None) -> Comment:
        self.counter += 1
        return Comment.objects.create(
            account=account or self.account,
            external_id=f"c{self.counter}",
            media_external_id=media,
            parent_external_id=parent,
            author_external_id=author,
            author_username=author,
            text=text,
            commented_at=timezone.now() - age,
        )

    def handle(self, comment: Comment) -> str:
        with mock.patch("apps.agent.tasks.send_reply.apply_async") as queued:
            with self.captureOnCommitCallbacks(execute=True):
                outcome = services.handle_new_comment(comment.pk)
        self.queued = queued
        return outcome

    def reply_ok(self, comment: Comment, reply_id="r1"):
        self.graph.on("POST", f"{comment.external_id}/replies", {"id": reply_id})

    def sent_reply(self, sent_at, text="Thanks!") -> CommentReply:
        return CommentReply.objects.create(
            comment=self.comment("old"), status=ReplyStatus.SENT, final_text=text, sent_at=sent_at
        )


class WebhookQueueTests(AgentTestCase):
    def _deliver(self, author_id="999", comment_id="c-web"):
        payload = {
            "object": "instagram",
            "entry": [{
                "id": IG_ID,
                "time": int(time.time()),
                "changes": [{"field": "comments", "value": {
                    "comment_id": comment_id, "text": "size M?", "from": {"id": author_id, "username": "fan"},
                    "media": {"id": "m1"},
                }}],
            }],
        }
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("webhook", args=["instagram"]), data=body, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    def test_new_comment_is_handed_to_the_agent_once(self):
        with mock.patch("apps.comments.services.handle_comment.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self._deliver().status_code, 200)
            with self.captureOnCommitCallbacks(execute=True):
                self._deliver()
        comment = Comment.objects.get(external_id="c-web")
        delay.assert_called_once_with(comment.pk)

    def test_own_comment_is_not_handed_to_the_agent(self):
        with mock.patch("apps.comments.services.handle_comment.delay") as delay:
            with self.captureOnCommitCallbacks(execute=True):
                self._deliver(author_id=IG_ID)
        delay.assert_not_called()


class UnknownPostTests(AgentTestCase):
    def _media(self, media_id="m9", caption="Posted from the app"):
        self.graph.on("GET", media_id, {
            "id": media_id, "caption": caption, "media_type": "IMAGE", "media_product_type": "FEED",
            "timestamp": "2026-11-16T10:00:00+0000",
        })

    def test_comment_on_unknown_post_fetches_it_for_the_agent(self):
        self._media()
        with mock.patch.object(services, "_context", wraps=services._context) as context:
            self.handle(self.comment("size?", media="m9"))
        post = Post.objects.get(account=self.account, external_id="m9")
        self.assertEqual(post.caption, "Posted from the app")
        self.assertEqual(context.call_args.kwargs["post"], post)
        self.assertTrue(CommentReply.objects.exists())

    def test_disabled_agent_still_stores_the_post(self):
        self.profile.enabled = False
        self.profile.save()
        self._media()
        self.assertEqual(self.handle(self.comment(media="m9")), "ignored")
        self.assertTrue(Post.objects.filter(external_id="m9").exists())

    def test_fetch_failure_still_decides_without_the_post(self):
        self.graph.on("GET", "m9", {"error": {"message": "Unsupported get request", "code": 100}}, status=400)
        with mock.patch.object(services, "_context", wraps=services._context) as context:
            self.handle(self.comment("size?", media="m9"))
        self.assertIsNone(context.call_args.kwargs["post"])
        self.assertTrue(CommentReply.objects.exists())


class DecisionTests(AgentTestCase):
    def test_disabled_agent_stores_nothing(self):
        self.profile.enabled = False
        self.profile.save()
        self.assertEqual(self.handle(self.comment("size?")), "ignored")
        self.assertFalse(CommentReply.objects.exists())

    def test_thread_reply_old_comment_and_disabled_post_are_skipped(self):
        PostBrain.objects.create(post=Post.objects.create(account=self.account, external_id="m2"), agent_enabled=False)
        cases = [
            (self.comment(parent="c0"), "Reply inside a thread."),
            (self.comment(age=timedelta(hours=30)), "The comment is too old to answer automatically."),
            (self.comment(media="m2"), "The agent is off for this post."),
        ]
        for comment, reason in cases:
            self.assertEqual(self.handle(comment), ReplyStatus.SKIPPED)
            self.assertEqual(comment.agent_reply.decision["reason"], reason)

    def test_spam_is_skipped(self):
        spam = AgentDecision(intent="spam", language="English", reply_text="", confidence=0.9, reason="Promo.")
        with mock.patch.object(services, "get_engine") as engine:
            engine.return_value.decide.return_value = spam
            engine.return_value.name = "fake"
            comment = self.comment("check my profile")
            self.assertEqual(self.handle(comment), ReplyStatus.SKIPPED)
        self.assertEqual(comment.agent_reply.decision["reason"], "Spam, not answered.")

    def test_answer_from_the_video_brain_sends_on_its_own(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock, XL sold out")
        comment = self.comment("is size XL available?")
        self.reply_ok(comment)
        with self.captureOnCommitCallbacks(execute=True):
            outcome = services.handle_new_comment(comment.pk)
        self.assertEqual(outcome, ReplyStatus.QUEUED)
        reply = CommentReply.objects.get(comment=comment)
        self.assertEqual(reply.status, ReplyStatus.SENT)
        self.assertEqual(reply.reply_external_id, "r1")
        method, path, data = self.graph.calls[-1]
        self.assertEqual((method, path), ("POST", f"{comment.external_id}/replies"))
        self.assertEqual(data["message"], "@fan1 Sizes S, M and L in stock, XL sold out")

    def test_praise_is_thanked_on_its_own(self):
        self.assertEqual(self.handle(self.comment("Loved it")), ReplyStatus.QUEUED)

    def test_missing_information_waits_for_review(self):
        comment = self.comment("which branch is this?")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("Missing information", comment.agent_reply.decision["reason"])
        self.queued.assert_not_called()

    def test_the_same_person_can_get_several_replies(self):
        self.assertEqual(self.handle(self.comment("shipping?")), ReplyStatus.QUEUED)
        self.assertEqual(self.handle(self.comment("returns?")), ReplyStatus.QUEUED)

    def test_repeated_identical_text_goes_to_review(self):
        for index in range(3):
            self.assertEqual(self.handle(self.comment("shipping?", author=f"fan{index}")), ReplyStatus.QUEUED)
        comment = self.comment("shipping?", author="fan9")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("same reply", comment.agent_reply.decision["reason"])

    def test_text_the_platform_would_reject_goes_to_review(self):
        self.profile.knowledge = "Links https://a.test and https://b.test"
        self.profile.save()
        comment = self.comment("links?")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("at most 1 link", comment.agent_reply.decision["reason"])


class SendingTests(AgentTestCase):
    def queued_reply(self, comment=None, scheduled_for=None) -> CommentReply:
        comment = comment or self.comment("size?")
        return CommentReply.objects.create(
            comment=comment, status=ReplyStatus.QUEUED, final_text="@fan1 Yes!",
            scheduled_for=scheduled_for or timezone.now(),
        )

    def test_replies_are_spaced_out(self):
        first = self.queued_reply()
        second = self.queued_reply(self.comment("size L?", author="fan2"))
        with mock.patch("apps.agent.tasks.send_reply.apply_async"):
            services.schedule_reply(first)
            services.schedule_reply(second)
        first.refresh_from_db()
        second.refresh_from_db()
        gap = (second.scheduled_for - first.scheduled_for).total_seconds()
        self.assertGreaterEqual(gap, 45)
        self.assertLessEqual(gap, 75)

    def test_hourly_cap_reschedules_without_calling_the_platform(self):
        oldest = timezone.now() - timedelta(minutes=50)
        for minute in range(20):
            self.sent_reply(oldest + timedelta(minutes=minute))
        reply = self.queued_reply()
        with mock.patch("apps.agent.tasks.send_reply.apply_async"):
            self.assertEqual(services.send_reply(reply.pk), "capped")
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.QUEUED)
        self.assertGreaterEqual(reply.scheduled_for, oldest + timedelta(hours=1))
        self.assertEqual(self.graph.calls, [])

    def test_rate_limit_pauses_the_account_and_requeues(self):
        reply = self.queued_reply()
        self.graph.on("POST", f"{reply.comment.external_id}/replies", {"error": {"code": 4, "message": "Too many calls"}}, status=400)
        with mock.patch("apps.agent.tasks.send_reply.apply_async"):
            self.assertEqual(services.send_reply(reply.pk), "rate_limited")
        reply.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.QUEUED)
        self.assertGreater(self.profile.paused_until, timezone.now() + timedelta(minutes=59))
        self.assertGreaterEqual(reply.scheduled_for, self.profile.paused_until)
        self.assertEqual(self.profile.pause_reason, "Too many calls")

    def test_expired_token_fails_the_reply_and_marks_the_account(self):
        reply = self.queued_reply()
        self.graph.on("POST", f"{reply.comment.external_id}/replies", {"error": {"code": 190, "message": "Expired"}}, status=400)
        self.assertEqual(services.send_reply(reply.pk), ReplyStatus.FAILED)
        self.account.refresh_from_db()
        self.assertEqual(self.account.status, SocialAccount.Status.EXPIRED)

    def test_a_reply_is_never_sent_twice(self):
        reply = self.queued_reply()
        self.reply_ok(reply.comment)
        self.assertEqual(services.send_reply(reply.pk), ReplyStatus.SENT)
        self.assertEqual(services.send_reply(reply.pk), "not_queued")
        self.assertEqual(self.graph.count("POST", f"{reply.comment.external_id}/replies"), 1)

    def test_early_task_waits_for_its_slot(self):
        reply = self.queued_reply(scheduled_for=timezone.now() + timedelta(minutes=2))
        self.assertEqual(services.send_reply(reply.pk), "not_due")
        self.assertEqual(self.graph.calls, [])

    def test_send_due_replies_command_sends_overdue_replies(self):
        reply = self.queued_reply(scheduled_for=timezone.now() - timedelta(minutes=10))
        self.reply_ok(reply.comment)
        call_command("send_due_replies", stdout=mock.Mock())
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.SENT)


class InstagramReplyTests(TestCase):
    def test_reply_text_problems(self):
        provider = InstagramProvider()
        self.assertEqual(provider.reply_text_problems("Thank you fam 🙏"), [])
        self.assertEqual(provider.reply_text_problems("OK 🙏"), [])
        self.assertTrue(provider.reply_text_problems("x" * 301))
        self.assertTrue(provider.reply_text_problems("#a #b #c #d #e"))
        self.assertTrue(provider.reply_text_problems("www.a.test https://b.test"))
        self.assertTrue(provider.reply_text_problems("THANK YOU SO MUCH"))
        self.assertTrue(provider.reply_text_problems("   "))


class InboxTests(AgentTestCase):
    def pending(self, text="@fan1 draft") -> CommentReply:
        return CommentReply.objects.create(
            comment=self.comment("size?"), status=ReplyStatus.PENDING_REVIEW, draft_text=text, final_text=text
        )

    def test_inbox_lists_replies_needing_review(self):
        self.pending()
        response = self.client.get(reverse("agent-inbox"))
        self.assertContains(response, "@fan1 draft")
        self.assertContains(response, 'Needs review <span class="tabs__count">1</span>')
        self.assertContains(response, "New kurta drop")
        self.assertContains(response, reverse("post-detail", args=[self.post.pk]))
        self.assertContains(response, "Send reply")

    def test_approve_sends_the_edited_text(self):
        reply = self.pending()
        self.reply_ok(reply.comment)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("api-reply-approve", args=[reply.pk]), {"text": "  @fan1 Edited  "})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["html"], "")
        self.assertIn('Needs review <span class="tabs__count">0</span>', data["fragments"]["inbox-tabs"])
        self.assertIn("All caught up", data["fragments"]["inbox-list"])
        self.assertNotIn("badge", data["fragments"]["nav-replies-count"])
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.SENT)
        self.assertEqual(reply.final_text, "@fan1 Edited")
        self.assertEqual(reply.draft_text, "@fan1 draft")

    def test_approve_rejects_text_the_platform_would_refuse(self):
        reply = self.pending()
        response = self.client.post(reverse("api-reply-approve", args=[reply.pk]), {"text": "x" * 301})
        self.assertContains(response, "at most 300 characters", status_code=400)
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.PENDING_REVIEW)

    def test_reject_and_retry(self):
        reply = self.pending()
        self.pending("@fan1 other")
        response = self.client.post(reverse("api-reply-reject", args=[reply.pk]))
        self.assertEqual(response.json()["message"], "Reply discarded.")
        self.assertNotIn("inbox-list", response.json()["fragments"])
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.REJECTED)

        reply.status = ReplyStatus.FAILED
        reply.save()
        with mock.patch("apps.agent.tasks.send_reply.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("api-reply-retry", args=[reply.pk]))
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.QUEUED)

    def test_sidebar_shows_pending_count(self):
        self.pending()
        self.assertContains(self.client.get(reverse("agent-inbox")), '<span class="badge badge--warning">1</span>', html=True)


class SetupPageTests(AgentTestCase):
    def test_settings_page_saves(self):
        page = self.client.get(reverse("agent-account", args=[self.account.pk]))
        self.assertContains(page, "What Hooky")
        self.assertContains(page, "Hooky is on")
        self.assertNotContains(page, "Special cases")
        url = reverse("api-agent-settings", args=[self.account.pk])
        response = self.client.post(url, {"voice": "Friendly", "knowledge": "Open 10 to 7"})
        self.assertEqual(response.json()["message"], "Agent settings saved.")
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.enabled)  # Only the switch turns the agent on or off.
        self.assertEqual(self.profile.knowledge, "Open 10 to 7")

    def test_switch_saves_at_once_and_redraws_the_status(self):
        url = reverse("api-agent-enabled", args=[self.account.pk])
        data = self.client.post(url, {"enabled": "on"}).json()
        self.assertEqual(data["message"], "Hooky is on.")
        self.assertIn("Hooky is on", data["fragments"]["agent-status"])
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.enabled)
        data = self.client.post(url, {}).json()
        self.assertIn("Hooky is off", data["fragments"]["agent-status"])
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.enabled)
        self.assertEqual(self.profile.knowledge, "Free shipping above Rs 999\nReturns within 7 days")

    def test_agent_home_redirects_to_first_account(self):
        self.assertRedirects(
            self.client.get(reverse("agent")), reverse("agent-account", args=[self.account.pk]), fetch_redirect_response=False
        )

    def test_video_brain_is_saved_from_the_post_page(self):
        with mock.patch("apps.content.services.post_insights", side_effect=lambda post, **_: post):
            self.assertContains(self.client.get(reverse("post-detail", args=[self.post.pk])), "Video brain")
        response = self.client.post(reverse("api-agent-post-brain", args=[self.post.pk]), {"knowledge": "Rs 1499"})
        self.assertEqual(response.json()["message"], "Video brain saved.")
        brain = PostBrain.objects.get(post=self.post)
        self.assertEqual(brain.knowledge, "Rs 1499")
        self.assertFalse(brain.agent_enabled)

    def test_preview_shows_the_reply_and_stores_nothing(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock")
        response = self.client.post(
            reverse("api-agent-preview", args=[self.account.pk]), {"text": "M size available?", "post": self.post.pk}
        )
        self.assertContains(response, "@customer Sizes S, M and L in stock")
        self.assertContains(response, "Replies automatically")
        self.assertFalse(CommentReply.objects.exists())
        self.assertEqual(self.graph.calls, [])

    def test_preview_uses_unsaved_brain_text(self):
        url = reverse("api-agent-preview", args=[self.account.pk])
        self.assertNotContains(self.client.post(url, {"text": "M size available?", "post": ""}), "Replies automatically")
        response = self.client.post(url, {"text": "M size available?", "post": "", "knowledge": "Sizes S, M and L in stock"})
        self.assertContains(response, "Replies automatically")
        self.profile.refresh_from_db()
        self.assertNotIn("Sizes", self.profile.knowledge)

    def test_other_users_get_404(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        self.client.force_login(other)
        reply = CommentReply.objects.create(comment=self.comment(), status=ReplyStatus.PENDING_REVIEW, final_text="hi")
        for method, url in [
            ("get", reverse("agent-account", args=[self.account.pk])),
            ("post", reverse("api-agent-settings", args=[self.account.pk])),
            ("post", reverse("api-agent-enabled", args=[self.account.pk])),
            ("post", reverse("api-agent-preview", args=[self.account.pk])),
            ("post", reverse("api-agent-post-brain", args=[self.post.pk])),
            ("post", reverse("api-reply-approve", args=[reply.pk])),
            ("post", reverse("api-reply-reject", args=[reply.pk])),
            ("post", reverse("api-reply-retry", args=[reply.pk])),
        ]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 404, url)


class ClaudeReplyEngineTests(AgentTestCase):
    def setUp(self):
        super().setUp()
        from apps.agent.engines.claude import ClaudeReplyEngine

        self.engine = ClaudeReplyEngine()

    def context(self, **fields):
        from apps.agent.engines.base import AgentContext

        defaults = {
            "comment_text": "price kya hai?",
            "author_username": "fan1",
            "account_username": "asha.store",
            "voice": "",
            "account_knowledge": "Free shipping above Rs 999",
            "post_knowledge": "Price: Rs 799",
        }
        return AgentContext(**{**defaults, **fields})

    @override_settings(AGENT_REPLY_MODEL="cheap-model")
    def test_decision_from_the_model_answer(self):
        answer = {"intent": "price", "language": "Hindi (Hinglish)", "reply_text": " @fan1 Rs 799 hai! ", "confidence": 1.4, "reason": "Price fact."}
        with mock.patch("apps.agent.engines.claude.generate_json", return_value=JsonAnswer(answer, TokenUsage("m"))) as call:
            decision = self.engine.decide(self.context())
        self.assertEqual(
            (decision.reply_text, decision.confidence, decision.language),
            ("@fan1 Rs 799 hai!", 1.0, "Hindi (Hinglish)"),
        )
        model, content = call.call_args.args
        self.assertEqual(model, "cheap-model")
        sent = json.loads(content)
        self.assertEqual(sent["post_facts"], "Price: Rs 799")
        self.assertNotIn("rules", sent)
        self.assertNotIn("voice", sent)
        self.assertEqual(call.call_args.kwargs["max_output_tokens"], 2000)

    def test_unreadable_confidence_is_zero(self):
        answer = {"intent": "x", "language": "English", "reply_text": "@fan1 hi", "confidence": "bad", "reason": ""}
        with mock.patch("apps.agent.engines.claude.generate_json", return_value=JsonAnswer(answer, TokenUsage("m"))):
            decision = self.engine.decide(self.context())
        self.assertEqual(decision.confidence, 0.0)

    def test_system_prompt_is_constant(self):
        from apps.agent.engines.prompts import REPLY_SCHEMA, REPLY_SYSTEM

        self.assertNotIn("{", REPLY_SYSTEM)
        self.assertNotIn("rule_id", REPLY_SCHEMA["properties"])

    def test_engine_failure_goes_to_review(self):
        from apps.core.claude import ClaudeError

        with override_settings(HOOKD_REPLY_ENGINE="apps.agent.engines.claude.ClaudeReplyEngine"):
            with mock.patch("apps.agent.engines.claude.generate_json", side_effect=ClaudeError("Down.")):
                outcome = self.handle(self.comment("price?"))
        self.assertEqual(outcome, ReplyStatus.PENDING_REVIEW)
        reply = CommentReply.objects.get()
        self.assertEqual(reply.draft_text, "")
        self.assertIn("Down.", reply.decision["reason"])

    def test_preview_shows_engine_failure(self):
        from apps.core.claude import ClaudeError

        with override_settings(HOOKD_REPLY_ENGINE="apps.agent.engines.claude.ClaudeReplyEngine"):
            with mock.patch("apps.agent.engines.claude.generate_json", side_effect=ClaudeError("Down.")):
                response = self.client.post(reverse("api-agent-preview", args=[self.account.pk]), {"text": "price?", "post": ""})
        self.assertContains(response, "Down.", status_code=400)


class LearningTests(AgentTestCase):
    """The brains fill themselves from captions and from replies the creator writes."""

    def test_synced_post_is_not_read_until_someone_comments_then_only_once(self):
        post = Post.objects.create(account=self.account, external_id="m2", caption="New drop! Kurta at Rs 799. Sizes 36 to 44 #ethnic\nDM to order")
        self.assertFalse(LearnedFact.objects.exists())

        self.handle(self.comment("Loved it", media="m2"))
        self.assertEqual(
            [fact.text for fact in LearnedFact.objects.order_by("pk")], ["Kurta at Rs 799.", "Sizes 36 to 44"]
        )
        self.assertTrue(all(fact.post == post and fact.source == "caption" for fact in LearnedFact.objects.all()))

        Post.objects.filter(pk=post.pk).update(caption="Price drop: now Rs 699")
        with mock.patch("apps.agent.engines.simulated.SimulatedBrainLearner.from_caption") as learner:
            self.handle(self.comment("Nice", media="m2"))
        learner.assert_not_called()
        self.assertEqual(LearnedFact.objects.count(), 2)

    def test_caption_read_works_with_the_agent_off(self):
        self.profile.enabled = False
        self.profile.save()
        self.assertEqual(self.handle(self.comment("hi", media="m1")), "ignored")
        self.assertTrue(PostBrain.objects.get(post=self.post).caption_learned_at)

    def test_post_found_through_a_comment_is_read(self):
        self.graph.on("GET", "m7", {"id": "m7", "caption": "Saree at Rs 1200", "media_type": "IMAGE", "media_product_type": "FEED"})
        self.handle(self.comment("Loved it", media="m7"))
        self.assertEqual(LearnedFact.objects.get().text, "Saree at Rs 1200")

    def test_post_published_from_hookd_is_read(self):
        from apps.content.models import PublishJob
        from apps.content.signals import post_published

        post = Post.objects.create(account=self.account, external_id="m3", caption="Bag at Rs 999")
        job = PublishJob.objects.create(account=self.account, kind="image", status="published", post=post)
        with self.captureOnCommitCallbacks(execute=True):
            post_published.send(sender=PublishJob, job=job, post=post)
        self.assertEqual(LearnedFact.objects.get().text, "Bag at Rs 999")

    def test_learner_failure_is_retried_on_the_next_comment(self):
        from apps.core.claude import ClaudeError

        with mock.patch("apps.agent.engines.simulated.SimulatedBrainLearner.from_caption", side_effect=ClaudeError()):
            self.assertEqual(services.learn_from_post(self.post.pk), "failed")
        self.assertIsNone(PostBrain.objects.get(post=self.post).caption_learned_at)
        self.post.caption = "Kurta at Rs 799"
        self.post.save()
        self.assertEqual(services.learn_from_post(self.post.pk), "learned 1")
        self.assertEqual(services.learn_from_post(self.post.pk), "already_learned")

    def test_learned_facts_answer_comments_and_typed_facts_win(self):
        PostBrain.objects.create(post=self.post, caption_learned_at=timezone.now())
        LearnedFact.objects.create(account=self.account, post=self.post, label="Sizes", text="Sizes S to XL", source="caption")
        LearnedFact.objects.create(account=self.account, label="Delivery", text="Delivery in 9 days", source="caption")
        self.profile.knowledge = "Delivery: 3 to 5 days across India"
        self.profile.save()
        comment = self.comment("is size XL available?")
        self.assertEqual(self.handle(comment), ReplyStatus.QUEUED)
        self.assertEqual(comment.agent_reply.final_text, "@fan1 Sizes: Sizes S to XL")

        preview = services.preview_reply(account=self.account, text="delivery time?", post=self.post)
        self.assertIn("3 to 5 days", preview.decision.reply_text)

    def test_rewritten_inbox_reply_teaches_the_next_answer(self):
        comment = self.comment("does it come in cotton?")
        reply = CommentReply.objects.create(
            comment=comment, status=ReplyStatus.PENDING_REVIEW, draft_text="@fan1 We will get back to you soon!",
            final_text="@fan1 We will get back to you soon!",
        )
        self.reply_ok(comment)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("api-reply-approve", args=[reply.pk]), {"text": "@fan1 Yes, 100% cotton"})
        fact = LearnedFact.objects.get()
        self.assertEqual(
            (fact.post, fact.source, fact.text, fact.source_reply), (self.post, "reply", "Yes, 100% cotton", reply)
        )
        self.assertFalse(AgentProfile.objects.get(pk=self.profile.pk).knowledge.count("cotton"))
        sent_tab = self.client.get(reverse("agent-inbox"), {"status": ReplyStatus.SENT})
        self.assertContains(sent_tab, "Hooky learned from your reply")
        self.assertContains(sent_tab, "this post only")
        self.assertContains(sent_tab, "Written by you, sent")
        self.assertNotContains(sent_tab, "Hooky was not sure")

        follow_up = self.comment("cotton?", author="fan2")
        self.assertEqual(self.handle(follow_up), ReplyStatus.QUEUED)
        self.assertEqual(follow_up.agent_reply.final_text, "@fan2 does it come in cotton?: Yes, 100% cotton")

    def test_approving_the_draft_unchanged_learns_nothing(self):
        reply = CommentReply.objects.create(
            comment=self.comment("hi?"), status=ReplyStatus.PENDING_REVIEW, draft_text="@fan1 Hello!", final_text="@fan1 Hello!"
        )
        with mock.patch("apps.agent.tasks.learn_from_reply.delay") as queued, mock.patch("apps.agent.tasks.send_reply.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("api-reply-approve", args=[reply.pk]), {"text": "@fan1 Hello!"})
        queued.assert_not_called()

    def test_same_topic_replaces_the_older_fact(self):
        from apps.agent import model_querysets as queries

        queries.save_learned_facts(self.account, [(None, "Delivery", "5 days")], source="caption", source_post=self.post)
        queries.save_learned_facts(self.account, [(None, "delivery", "3 days")], source="reply", source_post=None)
        self.assertEqual(list(LearnedFact.objects.values_list("text", flat=True)), ["3 days"])

    def test_pages_list_learned_facts_and_remove_them(self):
        shop = LearnedFact.objects.create(account=self.account, label="Returns", text="7 day returns", source="reply")
        LearnedFact.objects.create(account=self.account, post=self.post, label="Price", text="Rs 799", source="caption")
        page = self.client.get(reverse("agent-account", args=[self.account.pk]))
        self.assertContains(page, "7 day returns")
        self.assertContains(page, "Plus 1 fact about single posts")
        modal = self.client.get(reverse("api-post-preview", args=[self.post.pk])).json()["html"]
        self.assertIn("Rs 799", modal)
        Post.objects.filter(pk=self.post.pk).update(insights_fetched_at=timezone.now())
        self.assertContains(self.client.get(reverse("post-detail", args=[self.post.pk])), "Rs 799")

        other = create_user(email="other@example.com", password="a-strong-pass-123")
        self.client.force_login(other)
        self.assertEqual(self.client.post(reverse("api-agent-fact-forget", args=[shop.pk])).status_code, 404)
        self.client.force_login(self.user)
        response = self.client.post(reverse("api-agent-fact-forget", args=[shop.pk]))
        self.assertEqual(response.json()["html"], "")
        self.assertFalse(LearnedFact.objects.filter(pk=shop.pk).exists())


class ClaudeBrainLearnerTests(TestCase):
    @override_settings(AGENT_REPLY_MODEL="cheap-model")
    def test_facts_from_the_model_answer(self):
        from apps.agent.engines.claude import ClaudeBrainLearner
        from apps.agent.engines.prompts import LEARN_CAPTION_SYSTEM

        answer = {"facts": [
            {"scope": "post", "label": " Price ", "text": "Rs 799"},
            {"scope": "account", "label": "Delivery", "text": "3 to 5 days"},
            {"scope": "weird", "label": "Sizes", "text": "S to XL"},
            {"scope": "post", "label": "", "text": "no label"},
            "junk",
        ]}
        with mock.patch("apps.agent.engines.claude.generate_json", return_value=JsonAnswer(answer, TokenUsage("m"))) as call:
            facts = ClaudeBrainLearner().from_caption("Kurta Rs 799")
        self.assertEqual(
            [(fact.scope, fact.label, fact.text) for fact in facts],
            [("post", "Price", "Rs 799"), ("account", "Delivery", "3 to 5 days"), ("post", "Sizes", "S to XL")],
        )
        self.assertEqual(call.call_args.args, ("cheap-model", "Kurta Rs 799"))
        self.assertEqual(call.call_args.kwargs["system"], LEARN_CAPTION_SYSTEM)
        self.assertNotIn("{", LEARN_CAPTION_SYSTEM)
