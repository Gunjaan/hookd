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
from apps.agent.constants import FallbackAction, ReplyStatus, RuleAction
from apps.agent.models import AgentProfile, CommentReply, PostBrain, ReplyRule
from apps.comments.models import Comment
from apps.content.models import Post
from apps.content.tests import FakeGraph
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

    def rule(self, name="Size", keywords="size", action=RuleAction.AUTO_REPLY, post=None, how_to_reply="", position=0):
        return ReplyRule.objects.create(
            account=self.account, post=post, name=name, keywords=keywords, action=action,
            how_to_reply=how_to_reply, position=position,
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

    def test_ignore_rule_skips(self):
        self.rule(name="Spam", keywords="follow back", action=RuleAction.IGNORE)
        comment = self.comment("pls follow back")
        self.assertEqual(self.handle(comment), ReplyStatus.SKIPPED)
        self.assertIn('Rule "Spam" says ignore', comment.agent_reply.decision["reason"])

    def test_review_rule_drafts_from_the_video_brain(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock, XL sold out")
        self.rule(action=RuleAction.REVIEW)
        comment = self.comment("is size XL available?")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        reply = comment.agent_reply
        self.assertEqual(reply.draft_text, "@fan1 Sizes S, M and L in stock, XL sold out")
        self.assertEqual(reply.rule.name, "Size")
        self.queued.assert_not_called()

    def test_auto_rule_with_a_fact_sends(self):
        self.rule(keywords="shipping")
        comment = self.comment("is shipping free?")
        self.reply_ok(comment)
        with self.captureOnCommitCallbacks(execute=True):
            outcome = services.handle_new_comment(comment.pk)
        self.assertEqual(outcome, ReplyStatus.QUEUED)
        reply = CommentReply.objects.get(comment=comment)
        self.assertEqual(reply.status, ReplyStatus.SENT)
        self.assertEqual(reply.reply_external_id, "r1")
        method, path, data = self.graph.calls[-1]
        self.assertEqual((method, path), ("POST", f"{comment.external_id}/replies"))
        self.assertEqual(data["message"], "@fan1 Free shipping above Rs 999")

    def test_post_rule_beats_account_rule(self):
        self.rule(name="Account", keywords="price", action=RuleAction.IGNORE)
        self.rule(name="Post", keywords="price", action=RuleAction.REVIEW, post=self.post, how_to_reply="Rs 1499")
        comment = self.comment("price?")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertEqual(comment.agent_reply.rule.name, "Post")

    def test_unmatched_comment_follows_the_fallback(self):
        self.assertEqual(self.handle(self.comment("hello")), ReplyStatus.PENDING_REVIEW)
        self.profile.fallback_action = FallbackAction.IGNORE
        self.profile.save()
        self.assertEqual(self.handle(self.comment("hello again", author="fan2")), ReplyStatus.SKIPPED)

    def test_low_confidence_goes_to_review(self):
        self.rule(keywords="wow")
        comment = self.comment("wow")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("Low confidence", comment.agent_reply.decision["reason"])

    def test_second_auto_reply_to_the_same_person_on_a_post_goes_to_review(self):
        self.rule(keywords="shipping")
        self.assertEqual(self.handle(self.comment("shipping?")), ReplyStatus.QUEUED)
        comment = self.comment("shipping cost?")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("already got a reply", comment.agent_reply.decision["reason"])

    def test_repeated_identical_text_goes_to_review(self):
        self.rule(keywords="shipping")
        for index in range(3):
            self.assertEqual(self.handle(self.comment("shipping?", author=f"fan{index}")), ReplyStatus.QUEUED)
        comment = self.comment("shipping?", author="fan9")
        self.assertEqual(self.handle(comment), ReplyStatus.PENDING_REVIEW)
        self.assertIn("same reply", comment.agent_reply.decision["reason"])

    def test_text_the_platform_would_reject_goes_to_review(self):
        self.rule(keywords="link", how_to_reply="https://a.test and https://b.test")
        comment = self.comment("link?")
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
        self.assertContains(response, "Needs review (1)")

    def test_approve_sends_the_edited_text(self):
        reply = self.pending()
        self.reply_ok(reply.comment)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("agent-reply-approve", args=[reply.pk]), {"text": "  @fan1 Edited  "})
        self.assertEqual(response.status_code, 302)
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.SENT)
        self.assertEqual(reply.final_text, "@fan1 Edited")
        self.assertEqual(reply.draft_text, "@fan1 draft")

    def test_approve_rejects_text_the_platform_would_refuse(self):
        reply = self.pending()
        response = self.client.post(reverse("agent-reply-approve", args=[reply.pk]), {"text": "x" * 301}, follow=True)
        self.assertContains(response, "at most 300 characters")
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.PENDING_REVIEW)

    def test_reject_and_retry(self):
        reply = self.pending()
        self.client.post(reverse("agent-reply-reject", args=[reply.pk]))
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.REJECTED)

        reply.status = ReplyStatus.FAILED
        reply.save()
        with mock.patch("apps.agent.tasks.send_reply.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("agent-reply-retry", args=[reply.pk]))
        reply.refresh_from_db()
        self.assertEqual(reply.status, ReplyStatus.QUEUED)

    def test_sidebar_shows_pending_count(self):
        self.pending()
        self.assertContains(self.client.get(reverse("agent-inbox")), '<span class="badge badge--warning">1</span>', html=True)


class SetupPageTests(AgentTestCase):
    def test_settings_page_saves(self):
        url = reverse("agent-account", args=[self.account.pk])
        self.assertContains(self.client.get(url), "Account brain")
        response = self.client.post(url, {"fallback_action": "ignore", "voice": "Friendly", "knowledge": "Open 10 to 7"})
        self.assertRedirects(response, url)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.enabled)
        self.assertEqual(self.profile.fallback_action, "ignore")
        self.assertEqual(self.profile.knowledge, "Open 10 to 7")

    def test_agent_home_redirects_to_first_account(self):
        self.assertRedirects(
            self.client.get(reverse("agent")), reverse("agent-account", args=[self.account.pk]), fetch_redirect_response=False
        )

    def test_starter_rules_are_added_once(self):
        url = reverse("agent-starter-rules", args=[self.account.pk])
        self.client.post(url)
        self.assertEqual(ReplyRule.objects.filter(account=self.account).count(), len(services.STARTER_RULES))
        self.assertFalse(ReplyRule.objects.filter(action=RuleAction.AUTO_REPLY).exists())
        self.client.post(url)
        self.assertEqual(ReplyRule.objects.filter(account=self.account).count(), len(services.STARTER_RULES))

    def test_rule_create_edit_and_delete(self):
        response = self.client.post(
            reverse("agent-rule-new", args=[self.account.pk]) + f"?post={self.post.pk}",
            {"name": "Price", "keywords": "price", "action": "auto_reply", "how_to_reply": "Rs 1499", "enabled": "on"},
        )
        self.assertRedirects(response, reverse("post-detail", args=[self.post.pk]), fetch_redirect_response=False)
        rule = ReplyRule.objects.get(name="Price")
        self.assertEqual(rule.post, self.post)

        self.client.post(reverse("agent-rule-edit", args=[rule.pk]), {"name": "Price", "keywords": "price, cost", "action": "review"})
        rule.refresh_from_db()
        self.assertEqual((rule.keywords, rule.action, rule.enabled), ("price, cost", "review", False))

        self.client.post(reverse("agent-rule-delete", args=[rule.pk]))
        self.assertFalse(ReplyRule.objects.exists())

    def test_rule_needs_keywords_or_a_condition(self):
        response = self.client.post(reverse("agent-rule-new", args=[self.account.pk]), {"name": "Empty", "action": "review"})
        self.assertContains(response, "Add keywords or describe when the rule applies.")

    def test_video_brain_is_saved_from_the_post_page(self):
        with mock.patch("apps.content.services.post_insights", side_effect=lambda post, **_: post):
            self.assertContains(self.client.get(reverse("post-detail", args=[self.post.pk])), "Video brain")
        self.client.post(reverse("agent-post-brain", args=[self.post.pk]), {"knowledge": "Rs 1499"})
        brain = PostBrain.objects.get(post=self.post)
        self.assertEqual(brain.knowledge, "Rs 1499")
        self.assertFalse(brain.agent_enabled)

    def test_preview_shows_the_reply_and_stores_nothing(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock")
        self.rule(keywords="size")
        response = self.client.post(
            reverse("agent-preview", args=[self.account.pk]), {"text": "M size available?", "post": self.post.pk}
        )
        self.assertContains(response, "@customer Sizes S, M and L in stock")
        self.assertContains(response, "Replies automatically")
        self.assertFalse(CommentReply.objects.exists())
        self.assertEqual(self.graph.calls, [])

    def test_other_users_get_404(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        self.client.force_login(other)
        rule = self.rule()
        reply = CommentReply.objects.create(comment=self.comment(), status=ReplyStatus.PENDING_REVIEW, final_text="hi")
        for method, url in [
            ("get", reverse("agent-account", args=[self.account.pk])),
            ("get", reverse("agent-preview", args=[self.account.pk])),
            ("get", reverse("agent-rule-edit", args=[rule.pk])),
            ("post", reverse("agent-rule-delete", args=[rule.pk])),
            ("post", reverse("agent-post-brain", args=[self.post.pk])),
            ("post", reverse("agent-reply-approve", args=[reply.pk])),
            ("post", reverse("agent-reply-reject", args=[reply.pk])),
        ]:
            self.assertEqual(getattr(self.client, method)(url).status_code, 404, url)
