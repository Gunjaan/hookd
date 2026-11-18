import contextlib
import copy
import hashlib
import hmac
import json
import time
from datetime import timedelta
from unittest import mock

import requests
from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.model_querysets import create_user
from apps.agent import model_querysets as queries
from apps.agent import services, tools
from apps.agent.constants import DMKind, EventKind, QuestionStatus, ReplyStatus, RunStatus, TicketStatus
from apps.agent.engines.base import AgentModel, ModelStep
from apps.agent.models import (
    AgentAction,
    AgentEvent,
    AgentProfile,
    AgentRun,
    CommentReply,
    LearnedFact,
    OutgoingDM,
    PostBrain,
    SellerQuestion,
    SupportTicket,
)
from apps.comments.models import Comment
from apps.content.models import Post
from apps.content.tests import FakeGraph
from apps.integrations import model_querysets as account_queries
from apps.integrations.models import SocialAccount
from apps.integrations.providers.instagram.provider import InstagramProvider
from apps.integrations.providers.types import AccountProfile, IncomingMessage, TokenBundle
from apps.messaging.models import Conversation
from apps.messaging.services import record_incoming_messages

IG_ID = "17841400000000"
INSTAGRAM_TEST_SETTINGS = {
    "APP_ID": "123",
    "APP_SECRET": "secret",
    "LOGIN_CONFIG_ID": "456",
    "REDIRECT_URI": "https://hookd.test/auth/instagram/callback",
    "GRAPH_API_VERSION": "v27.0",
    "WEBHOOK_VERIFY_TOKEN": "verify-me",
}


def call(name: str, call_id: str = "", **arguments) -> ModelStep:
    """A model turn that calls one tool."""
    return ModelStep([{"type": "tool_use", "id": call_id or f"tu_{name}", "name": name, "input": arguments}], "tool_use")


def calls(*steps: ModelStep) -> ModelStep:
    """A model turn that calls several tools at once."""
    content = []
    for index, step in enumerate(steps):
        block = dict(step.content[0])
        block["id"] = f"{block['id']}_{index}"
        content.append(block)
    return ModelStep(content, "tool_use")


def done(text: str = "Done.") -> ModelStep:
    return ModelStep([{"type": "text", "text": text}], "end_turn")


class ScriptedModel(AgentModel):
    """Plays back model turns in order and keeps every transcript it was sent."""

    name = "scripted"

    def __init__(self, *steps, on_step=None):
        self.steps = list(steps)
        self.seen = []
        self.on_step = on_step

    def step(self, *, system, tools, messages):
        self.seen.append(copy.deepcopy(messages))
        if self.on_step:
            self.on_step(len(self.seen))
        return self.steps.pop(0)

    def last_user_text(self) -> str:
        """The newest user turn of the latest call."""
        return json.dumps(self.seen[-1][-1]["content"])

    def first_user_text(self) -> str:
        """The user turn that woke the model up (the newest one of its first call)."""
        return json.dumps(self.seen[0][-1]["content"])


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

    def handle(self, comment: Comment, model: AgentModel | None = None) -> str:
        """Start the agent on a comment and let the run finish. Sending is not run (see `self.queued`)."""
        with self.scripted(model), mock.patch("apps.agent.tasks.send_reply.apply_async") as queued, \
                mock.patch("apps.agent.tasks.send_dm.apply_async") as queued_dm:
            with self.captureOnCommitCallbacks(execute=True):
                outcome = services.handle_new_comment(comment.pk)
        self.queued, self.queued_dm = queued, queued_dm
        return outcome

    def scripted(self, model: AgentModel | None):
        if model is None:
            return contextlib.nullcontext()  # the simulated model (settings in tests)
        return mock.patch("apps.agent.loop.get_model", return_value=model)

    def run_events(self, run: AgentRun, model: AgentModel | None = None) -> str:
        with self.scripted(model), mock.patch("apps.agent.tasks.send_reply.apply_async"), \
                mock.patch("apps.agent.tasks.send_dm.apply_async") as queued_dm:
            with self.captureOnCommitCallbacks(execute=True):
                outcome = services.advance_run(run.pk)
        self.queued_dm = queued_dm
        return outcome

    def buyer_dm(self, text: str, *, sender="fan1", model: AgentModel | None = None, mid="", **fields) -> None:
        self.counter += 1
        incoming = IncomingMessage(
            account_external_id=IG_ID, external_id=mid or f"mid{self.counter}", participant_external_id=sender,
            text=text, sent_at=timezone.now(), **fields,
        )
        with self.scripted(model), mock.patch("apps.agent.tasks.send_reply.apply_async"), \
                mock.patch("apps.agent.tasks.send_dm.apply_async") as queued_dm:
            with self.captureOnCommitCallbacks(execute=True):
                record_incoming_messages(platform="instagram", messages=[incoming])
        self.queued_dm = queued_dm

    def tools_used(self, run: AgentRun) -> list[tuple[str, bool]]:
        return [(action.tool, action.is_error) for action in AgentAction.objects.filter(run=run)]

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
        self._media(caption="Kurta at Rs 650")
        model = ScriptedModel(call("get_post", post_id=""), done())
        self.assertEqual(self.handle(self.comment("price?", media="m9"), model), "started")
        post = Post.objects.get(account=self.account, external_id="m9")
        self.assertEqual(post.caption, "Kurta at Rs 650")
        self.assertIn("Kurta at Rs 650", model.last_user_text())

    def test_disabled_agent_still_stores_the_post(self):
        self.profile.enabled = False
        self.profile.save()
        self._media()
        self.assertEqual(self.handle(self.comment(media="m9")), "ignored")
        self.assertTrue(Post.objects.filter(external_id="m9").exists())
        self.assertFalse(AgentRun.objects.exists())

    def test_fetch_failure_still_runs_without_the_post(self):
        self.graph.on("GET", "m9", {"error": {"message": "Unsupported get request", "code": 100}}, status=400)
        model = ScriptedModel(call("get_post", post_id=""), done())
        self.handle(self.comment("size?", media="m9"), model)
        self.assertEqual(self.tools_used(AgentRun.objects.get()), [("get_post", True)])


class SkipTests(AgentTestCase):
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
        self.assertFalse(AgentRun.objects.exists())


class LoopTests(AgentTestCase):
    def test_answer_from_the_video_brain_is_sent(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock, XL sold out")
        comment = self.comment("is size XL available?")
        self.reply_ok(comment)
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(services.handle_new_comment(comment.pk), "started")
        reply = CommentReply.objects.get(comment=comment)
        self.assertEqual((reply.status, reply.reply_external_id), (ReplyStatus.SENT, "r1"))
        method, path, data = self.graph.calls[-1]
        self.assertEqual((method, path), ("POST", f"{comment.external_id}/replies"))
        self.assertEqual(data["message"], "@fan1 Sizes S, M and L in stock, XL sold out")
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.DONE)
        self.assertEqual(self.tools_used(run), [("search_brain", False), ("reply_to_comment", False)])

    def test_praise_gets_a_public_thank_you(self):
        comment = self.comment("Loved it")
        self.handle(comment)
        self.assertEqual(comment.agent_reply.status, ReplyStatus.QUEUED)
        self.assertEqual(comment.agent_reply.final_text, "@fan1 Thank you so much!")
        self.queued.assert_called_once()

    def test_invented_price_is_refused_and_the_agent_corrects_itself(self):
        model = ScriptedModel(
            call("reply_to_comment", text="@fan1 Rs 1,299 only!"),
            call("search_brain", topic="price"),
            call("reply_to_comment", text="@fan1 Free shipping above Rs 999"),
            done("Answered shipping."),
        )
        comment = self.comment("price?")
        self.handle(comment, model)
        refused = AgentAction.objects.filter(tool="reply_to_comment").first()
        self.assertTrue(refused.is_error)
        self.assertEqual(refused.result, "1299 is not in the brain, use search_brain or ask_seller.")
        self.assertIn("1299 is not in the brain", json.dumps(model.seen[1][-1]["content"]))
        self.assertEqual(comment.agent_reply.final_text, "@fan1 Free shipping above Rs 999")
        self.assertEqual(AgentRun.objects.get().outcome, "Answered shipping.")

    def test_numbers_from_the_comment_are_allowed_and_handles_ignored(self):
        model = ScriptedModel(call("reply_to_comment", text="@fan2024 Size 42 noted!"), done())
        comment = self.comment("size 42 milega?", author="fan2024")
        self.handle(comment, model)
        self.assertEqual(comment.agent_reply.final_text, "@fan2024 Size 42 noted!")

    def test_missing_fact_asks_the_seller_and_the_answer_resumes_the_run(self):
        model = ScriptedModel(
            call("search_brain", topic="fabric"),
            call("ask_seller", question="What fabric is the kurta?"),
            calls(
                call("save_fact", label="Fabric", fact="Pure cotton", scope="post"),
                call("reply_to_comment", text="@fan1 Pure cotton hai!"),
            ),
            done("Asked you, then answered."),
        )
        comment = self.comment("fabric kya hai?")
        self.handle(comment, model)
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.WAITING_SELLER)
        self.assertFalse(CommentReply.objects.exists())
        question = SellerQuestion.objects.get()
        self.assertEqual(question.question, "What fabric is the kurta?")

        inbox = self.client.get(reverse("agent-inbox"))
        self.assertContains(inbox, "What fabric is the kurta?")
        self.assertContains(inbox, 'Hooky asks <span class="tabs__count">1</span>')
        with self.scripted(model), mock.patch("apps.agent.tasks.send_reply.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("api-question-answer", args=[question.pk]), {"answer": "Pure cotton"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("All caught up", response.json()["fragments"]["inbox-list"])
        answer_turn = model.seen[2][-1]["content"]
        self.assertEqual(answer_turn, [{"type": "tool_result", "tool_use_id": "tu_ask_seller", "content": "The seller answered: Pure cotton"}])
        fact = LearnedFact.objects.get()
        self.assertEqual((fact.label, fact.post, fact.source, fact.source_run), ("Fabric", self.post, "agent", run))
        comment.refresh_from_db()
        self.assertEqual(comment.agent_reply.status, ReplyStatus.QUEUED)
        run.refresh_from_db()
        self.assertEqual((run.status, run.outcome), (RunStatus.DONE, "Asked you, then answered."))

    def test_held_results_go_back_with_the_answer(self):
        model = ScriptedModel(
            calls(call("search_brain", topic="fabric"), call("ask_seller", question="Fabric?")),
            done(),
        )
        self.handle(self.comment("fabric?"), model)
        question = SellerQuestion.objects.get()
        services.answer_question(question, "Silk")
        self.run_events(question.run, model)
        turn = model.seen[1][-1]["content"]
        self.assertEqual([block["tool_use_id"] for block in turn], ["tu_search_brain_0", "tu_ask_seller_1"])
        self.assertEqual(turn[1]["content"], "The seller answered: Silk")

    def test_buyer_writing_while_hooky_waits_gets_an_answer_and_the_seller_answer_comes_later(self):
        first = ScriptedModel(
            calls(call("search_brain", topic="custom text"), call("ask_seller", question="Custom text on mugs?")),
            done(),
        )
        self.buyer_dm("can you print aspire on it?", sender="buyer9", model=first)
        run = AgentRun.objects.get()
        self.assertEqual(run.status, RunStatus.WAITING_SELLER)

        waiting = ScriptedModel(call("send_dm", text="Still checking with the shop!"), done())
        self.buyer_dm("??", sender="buyer9", model=waiting)
        turn = waiting.first_user_text()
        self.assertIn("tu_search_brain_0", turn)
        self.assertIn("No answer from the seller yet", turn)
        self.assertIn("??", turn)
        self.assertEqual(OutgoingDM.objects.count(), 1)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.WAITING_SELLER)

        answered = ScriptedModel(call("send_dm", text="Yes, Rs 250 with your text!"), done())
        question = SellerQuestion.objects.get()
        with self.scripted(answered), mock.patch("apps.agent.tasks.send_dm.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                services.answer_question(question, "Yes, custom text is Rs 250")
        self.assertIn(
            'Answer to your earlier question to the seller (\\"Custom text on mugs?\\"): The seller answered: Yes, custom text is Rs 250',
            answered.first_user_text(),
        )
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE)

    def test_dismissed_question_tells_the_agent_to_wrap_up(self):
        model = ScriptedModel(call("ask_seller", question="Fabric?"), done("Left it."))
        self.handle(self.comment("fabric?"), model)
        question = SellerQuestion.objects.get()
        with self.scripted(model):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(reverse("api-question-dismiss", args=[question.pk]))
        self.assertIn("chose not to answer", model.last_user_text())
        question.refresh_from_db()
        self.assertEqual(question.status, QuestionStatus.DISMISSED)
        self.assertEqual(CommentReply.objects.get().status, ReplyStatus.SKIPPED)

    def test_spam_is_left_alone_and_shows_as_skipped(self):
        comment = self.comment("check my profile for followers")
        self.handle(comment, ScriptedModel(done("Spam, ignored.")))
        self.assertEqual(comment.agent_reply.status, ReplyStatus.SKIPPED)
        self.assertEqual(comment.agent_reply.decision["reason"], "Spam, ignored.")

    def test_model_failure_goes_to_review(self):
        from apps.ai.claude import ClaudeError

        broken = mock.Mock(spec=AgentModel)
        broken.step.side_effect = ClaudeError("Down.")
        comment = self.comment("price?")
        self.handle(comment, broken)
        self.assertEqual(AgentRun.objects.get().status, RunStatus.FAILED)
        self.assertEqual(comment.agent_reply.status, ReplyStatus.PENDING_REVIEW)
        self.assertIn("Down.", comment.agent_reply.decision["reason"])

    def test_step_limit_fails_the_run(self):
        model = ScriptedModel(*[call("search_brain", topic="x") for _ in range(8)])
        self.handle(self.comment("price?"), model)
        run = AgentRun.objects.get()
        self.assertEqual((run.status, run.steps), (RunStatus.FAILED, 8))

    def test_one_public_reply_per_comment_and_platform_rules(self):
        model = ScriptedModel(
            call("reply_to_comment", text="x" * 301),
            call("reply_to_comment", text="@fan1 Thanks!"),
            call("reply_to_comment", text="@fan1 Thanks again!"),
            done(),
        )
        self.handle(self.comment("nice"), model)
        self.assertEqual(
            self.tools_used(AgentRun.objects.get()),
            [("reply_to_comment", True), ("reply_to_comment", False), ("reply_to_comment", True)],
        )
        self.assertIn("already replied", AgentAction.objects.last().result)

    def test_repeated_identical_text_is_refused(self):
        for index in range(3):
            CommentReply.objects.create(
                comment=self.comment("old", author=f"x{index}"), status=ReplyStatus.SENT, final_text=f"@x{index} Thanks!",
                sent_at=timezone.now(),
            )
        self.handle(self.comment("nice"), ScriptedModel(call("reply_to_comment", text="@fan1 Thanks!"), done()))
        self.assertIn("exact reply", AgentAction.objects.get().result)

    def test_approval_mode_holds_replies_and_dms(self):
        self.profile.approve_before_sending = True
        self.profile.save()
        model = ScriptedModel(
            calls(call("reply_to_comment", text="@fan1 Thank you!"), call("send_dm", text="Hi! Want the size chart?")),
            done(),
        )
        comment = self.comment("love the colour")
        self.handle(comment, model)
        self.assertEqual(comment.agent_reply.status, ReplyStatus.PENDING_REVIEW)
        self.assertTrue(comment.agent_reply.decision["approval"])
        dm = OutgoingDM.objects.get()
        self.assertEqual((dm.status, dm.kind), (ReplyStatus.PENDING_REVIEW, DMKind.PRIVATE_REPLY))
        self.queued.assert_not_called()
        self.queued_dm.assert_not_called()

        inbox = self.client.get(reverse("agent-inbox"), {"status": ReplyStatus.PENDING_REVIEW})
        self.assertContains(inbox, "Hi! Want the size chart?")
        self.assertContains(inbox, 'Needs review <span class="tabs__count">2</span>')
        self.assertContains(inbox, "What Hooky did")
        with mock.patch("apps.agent.tasks.send_dm.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(reverse("api-dm-approve", args=[dm.pk]), {"text": "Hi! Size chart?"})
        self.assertEqual(response.status_code, 200)
        dm.refresh_from_db()
        self.assertEqual((dm.status, dm.text), (ReplyStatus.QUEUED, "Hi! Size chart?"))

    def test_discarding_a_private_reply_frees_it(self):
        self.profile.approve_before_sending = True
        self.profile.save()
        self.handle(self.comment("nice"), ScriptedModel(call("send_dm", text="Hello!"), done()))
        dm = OutgoingDM.objects.get()
        self.client.post(reverse("api-dm-reject", args=[dm.pk]))
        run = AgentRun.objects.get()
        self.assertIsNone(run.private_reply_used_at)
        self.assertEqual(services.dm_route(run, timezone.now())[0], DMKind.PRIVATE_REPLY)

    def test_busy_run_leaves_new_events_to_the_worker_holding_it(self):
        comment = self.comment("nice")
        run, _ = queries.create_run_if_new(comment, buyer_external_id="fan1", buyer_username="fan1")
        queries.add_event(run, EventKind.COMMENT)
        queries.claim_run(run.pk, stale_before=timezone.now() - timedelta(minutes=10))
        self.assertEqual(services.advance_run(run.pk), "busy")
        AgentRun.objects.filter(pk=run.pk).update(status=RunStatus.QUEUED)

        def late_event(step_number):
            if step_number == 1:
                queries.add_event(run, EventKind.FOLLOWUP, {"note": "check back"})

        model = ScriptedModel(done("first"), done("second"), on_step=late_event)
        self.assertEqual(self.run_events(run, model), RunStatus.DONE)
        self.assertEqual(len(model.seen), 2)
        self.assertIn("check back", model.last_user_text())


class DirectMessageTests(AgentTestCase):
    def private_reply_ok(self, recipient="fan1"):
        self.graph.on("POST", f"{IG_ID}/messages", {"message_id": "mid-out", "recipient_id": recipient})

    def test_private_reply_goes_out_once_and_links_the_conversation(self):
        model = ScriptedModel(
            calls(call("send_dm", text="Hi! Sizes S to L, DM to order."), call("send_dm", text="Also free shipping!")),
            done(),
        )
        self.profile.knowledge = "Sizes S to L"
        self.profile.save()
        comment = self.comment("interested")
        self.handle(comment, model)
        self.assertEqual(self.tools_used(AgentRun.objects.get()), [("send_dm", False), ("send_dm", True)])
        self.assertIn("private reply to this comment was already used", AgentAction.objects.last().result)

        dm = OutgoingDM.objects.get()
        self.private_reply_ok()
        self.assertEqual(services.send_dm(dm.pk), ReplyStatus.SENT)
        _, _, data = self.graph.calls[-1]
        self.assertEqual(json.loads(data["recipient"]), {"comment_id": comment.external_id})
        self.assertEqual(json.loads(data["message"]), {"text": "Hi! Sizes S to L, DM to order."})
        run = AgentRun.objects.get()
        self.assertEqual(run.conversation.participant_external_id, "fan1")
        self.assertEqual(services.send_dm(dm.pk), "not_queued")

    def test_private_reply_window_is_seven_days(self):
        for age, allowed in ((timedelta(days=6), True), (timedelta(days=7), False)):
            run = AgentRun(account=self.account, comment=self.comment(age=age))
            self.assertEqual(services.dm_route(run, timezone.now())[0] == DMKind.PRIVATE_REPLY, allowed, age)

    def test_buyer_answer_continues_the_run_with_a_normal_dm(self):
        self.handle(self.comment("interested"), ScriptedModel(call("send_dm", text="Hi! Want details?"), done()))
        self.private_reply_ok()
        services.send_dm(OutgoingDM.objects.get().pk)

        model = ScriptedModel(call("send_dm", text="Here you go!"), done("Answered their DM."))
        self.buyer_dm("yes please", model=model)
        self.assertIn("New DM from the buyer", model.first_user_text())
        self.assertIn("A DM is allowed now.", model.first_user_text())
        dms = OutgoingDM.objects.order_by("pk")
        self.assertEqual([dm.kind for dm in dms], [DMKind.PRIVATE_REPLY, DMKind.DM])
        self.assertEqual(AgentRun.objects.count(), 1)

        self.graph.on("POST", f"{IG_ID}/messages", {"message_id": "mid-2", "recipient_id": "fan1"})
        OutgoingDM.objects.filter(pk=dms[1].pk).update(scheduled_for=timezone.now())  # its slot has come
        self.assertEqual(services.send_dm(dms[1].pk), ReplyStatus.SENT)
        _, _, data = self.graph.calls[-1]
        self.assertEqual(json.loads(data["recipient"]), {"id": "fan1"})

    def test_dm_outside_the_window_fails_at_send_time(self):
        self.handle(self.comment("interested"), ScriptedModel(call("send_dm", text="Hi!"), done()))
        dm = OutgoingDM.objects.get()
        Comment.objects.filter(pk=dm.run.comment_id).update(commented_at=timezone.now() - timedelta(days=8))
        self.assertEqual(services.send_dm(dm.pk), ReplyStatus.FAILED)
        self.assertEqual(self.graph.calls, [])

    def test_dm_problem_is_ticketed_after_asking_for_the_product_then_status_is_shared(self):
        self.comment("love it", author="buyer9")
        first = ScriptedModel(call("check_tickets"), call("send_dm", text="So sorry! Which product was it?"), done())
        self.buyer_dm("my order came damaged", sender="buyer9", model=first)
        run = AgentRun.objects.get()
        self.assertIsNone(run.comment)
        self.assertEqual((run.buyer_external_id, run.buyer_username), ("buyer9", "buyer9"))
        self.assertIn("A buyer wrote the seller a DM.", json.dumps(first.seen[0]))
        self.assertEqual(AgentAction.objects.first().result, "This buyer has no tickets.")
        self.assertEqual(OutgoingDM.objects.get().kind, DMKind.DM)

        second = ScriptedModel(
            call("create_ticket", product="", summary="Damaged order"),
            call("create_ticket", product="Blue kurta", summary="Arrived torn, wants a replacement"),
            done("Opened a ticket."),
        )
        self.buyer_dm("the blue kurta", sender="buyer9", model=second)
        ticket = SupportTicket.objects.get()
        self.assertIn("Which product", AgentAction.objects.filter(tool="create_ticket").first().result)
        self.assertEqual((ticket.product, ticket.buyer_username, ticket.status), ("Blue kurta", "buyer9", TicketStatus.OPEN))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(f"Ticket #{ticket.pk}", mail.outbox[0].subject)
        self.assertIn("Blue kurta", mail.outbox[0].body)
        self.assertIsNotNone(SupportTicket.objects.get().emailed_at)

        page = self.client.get(reverse("agent-tickets"))
        self.assertContains(page, "Arrived torn")
        response = self.client.post(
            reverse("api-ticket-update", args=[ticket.pk]),
            {f"t{ticket.pk}-status": "in_progress", f"t{ticket.pk}-seller_note": "Replacement ships Monday"},
        )
        self.assertEqual(response.status_code, 200)

        third = ScriptedModel(call("check_tickets"), call("send_dm", text=f"Ticket #{ticket.pk} is in progress."), done())
        self.buyer_dm("any update?", sender="buyer9", model=third)
        status = AgentAction.objects.filter(tool="check_tickets").last().result
        self.assertIn("Status: In progress", status)
        self.assertIn("Replacement ships Monday", status)
        self.assertFalse(AgentAction.objects.filter(tool="send_dm", is_error=True).exists())

    def share(self, text: str, post_id: str, caption: str, model: AgentModel) -> None:
        self.buyer_dm(
            text, sender="buyer9", model=model, kind="share", shared_post_external_id=post_id, shared_post_caption=caption,
        )

    def test_shared_post_is_fetched_and_becomes_the_runs_post(self):
        self.graph.on("GET", "m9", {
            "id": "m9", "caption": "Custom mug, any text, Rs 499", "media_type": "IMAGE", "media_product_type": "FEED",
            "timestamp": "2026-11-16T10:00:00+0000",
        })
        model = ScriptedModel(call("get_post", post_id=""), done())
        self.share("same cup with aspire?", "m9", "Your first coffee deserves a mug", model)
        self.assertIn('shared a post, post_id m9, caption: \\"Your first coffee deserves a mug\\"', model.first_user_text())
        self.assertIn("same cup with aspire?", model.first_user_text())
        self.assertEqual(self.tools_used(AgentRun.objects.get()), [("get_post", False)])
        self.assertIn("Custom mug, any text, Rs 499", AgentAction.objects.get().result)

        later = ScriptedModel(call("get_post", post_id="m9"), done())
        self.buyer_dm("and in black?", sender="buyer9", model=later)
        self.assertIn("Custom mug", AgentAction.objects.last().result)

    def test_someone_elses_shared_post_still_gives_its_caption(self):
        self.graph.on("GET", "x1", {"error": {"message": "Unsupported get request", "code": 100}}, status=400)
        model = ScriptedModel(call("get_post", post_id=""), done())
        self.share("", "x1", "Mug from another shop", model)
        self.assertIn("Mug from another shop", model.first_user_text())
        self.assertEqual(self.tools_used(AgentRun.objects.get()), [("get_post", True)])
        self.assertFalse(Post.objects.filter(external_id="x1").exists())

    @override_settings(AGENT_LOOP_LIMITS={**settings.AGENT_LOOP_LIMITS, "DM_BATCH_SECONDS": 10})
    def test_dms_sent_together_are_read_in_one_turn(self):
        early = ScriptedModel(done())
        self.share("", "m1", "New kurta drop", early)
        self.buyer_dm("same one with custom text?", sender="buyer9", model=early)
        self.assertEqual(early.seen, [])
        events = AgentEvent.objects.filter(kind=EventKind.DM)
        self.assertEqual(len({event.available_at for event in events}), 1)
        self.assertGreater(events.first().available_at, timezone.now())

        events.update(available_at=timezone.now())
        model = ScriptedModel(done())
        self.run_events(AgentRun.objects.get(), model)
        turn = model.first_user_text()
        self.assertIn("shared a post, post_id m1", turn)
        self.assertIn("same one with custom text?", turn)
        self.assertEqual(AgentRun.objects.get().wake_at, None)

    def test_get_history_shows_earlier_dms_both_ways_and_comments(self):
        self.comment("price?", author="buyer9")
        self.buyer_dm("can you print aspire on it?", sender="buyer9", model=ScriptedModel(done()))
        conversation = AgentRun.objects.get().conversation
        record_incoming_messages(platform="instagram", messages=[IncomingMessage(
            account_external_id=IG_ID, external_id="mid-seller", participant_external_id="buyer9", is_from_account=True,
            text="Yes we can, Rs 250", sent_at=timezone.now(),
        )])
        model = ScriptedModel(call("get_history"), done())
        self.buyer_dm("??", sender="buyer9", model=model)
        history = AgentAction.objects.get(tool="get_history").result
        self.assertIn("on post m1: price?", history)
        self.assertIn("Buyer: can you print aspire on it?", history)
        self.assertIn("Shop: Yes we can, Rs 250", history)
        self.assertIn("Buyer: ??", history)
        self.assertLess(history.index("aspire"), history.index("Rs 250"))
        self.assertEqual(conversation.messages.count(), 3)

    def test_get_history_without_earlier_messages(self):
        comment = self.comment("first time here", author="newbie")
        self.handle(comment, ScriptedModel(call("get_history"), done()))
        self.assertIn("on post m1: first time here", AgentAction.objects.get().result)
        self.assertNotIn("DMs:", AgentAction.objects.get().result)

    def test_dm_run_cannot_reply_publicly(self):
        self.buyer_dm("hi", sender="buyer9", model=ScriptedModel(call("reply_to_comment", text="@x hi"), done()))
        self.assertIn("no comment to reply to", AgentAction.objects.get().result)

    def test_dms_are_ignored_with_the_agent_off(self):
        self.profile.enabled = False
        self.profile.save()
        self.buyer_dm("hi", sender="buyer9")
        self.assertFalse(AgentRun.objects.exists())


class FollowupTests(AgentTestCase):
    def test_followup_is_scheduled_within_meta_limits_and_wakes_the_run(self):
        model = ScriptedModel(
            call("schedule_followup", hours=200, note="too late"),
            call("schedule_followup", hours=24, note="send sizes and price"),
            call("schedule_followup", hours=30, note="again"),
            done(),
        )
        self.handle(self.comment("looks good!"), model)
        run = AgentRun.objects.get()
        self.assertEqual(self.tools_used(run), [("schedule_followup", True), ("schedule_followup", False), ("schedule_followup", True)])
        self.assertIn("already scheduled", AgentAction.objects.last().result)
        self.assertEqual(run.status, RunStatus.WAITING_FOLLOWUP)
        self.assertGreater(run.wake_at, timezone.now() + timedelta(hours=23))

        AgentEvent.objects.filter(kind=EventKind.FOLLOWUP).update(available_at=timezone.now() - timedelta(minutes=10))
        AgentRun.objects.filter(pk=run.pk).update(wake_at=timezone.now() - timedelta(minutes=10))
        later = ScriptedModel(call("send_dm", text="Hi! Sizes S to L are in stock."), done("Followed up."))
        with self.scripted(later), mock.patch("apps.agent.tasks.send_dm.apply_async"):
            call_command("wake_agent_runs", stdout=mock.Mock())
        self.assertIn("send sizes and price", later.first_user_text())
        self.assertIn("private reply", later.first_user_text())
        run.refresh_from_db()
        self.assertEqual((run.status, run.wake_at), (RunStatus.DONE, None))
        self.assertEqual(OutgoingDM.objects.get().kind, DMKind.PRIVATE_REPLY)

    def test_no_followup_once_a_dm_is_no_longer_allowed(self):
        comment = self.comment("nice")
        run = AgentRun.objects.create(account=self.account, comment=comment, private_reply_used_at=timezone.now())
        ctx = tools.ToolContext(run=run, profile=self.profile, post=self.post)
        result = tools.schedule_followup(ctx, 5, "nudge")
        self.assertTrue(result.is_error)
        self.assertIn("would not allow a DM", result.text)


class NumberGuardTests(AgentTestCase):
    def test_numbers_must_come_from_what_the_agent_was_told(self):
        run = AgentRun(account=self.account, comment=self.comment(), messages=[
            {"role": "user", "content": [{"type": "text", "text": "Comment: price for size 40?"}]},
            {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "search_brain", "input": {}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "Price: Rs 1,499.00"}]},
        ])
        ctx = tools.ToolContext(run=run, profile=self.profile, post=self.post)
        self.assertEqual(tools.unbacked_numbers(ctx, "@shop99 Rs 1499 for size 40"), [])
        self.assertEqual(tools.unbacked_numbers(ctx, "Rs 1,499, now 10% off till 25 Nov"), ["10", "25"])


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
        self.assertContains(page, "Check every reply and DM before it goes out")
        url = reverse("api-agent-settings", args=[self.account.pk])
        response = self.client.post(url, {"voice": "Friendly", "knowledge": "Open 10 to 7", "approve_before_sending": "on"})
        self.assertEqual(response.json()["message"], "Agent settings saved.")
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.enabled)  # Only the switch turns the agent on or off.
        self.assertEqual(self.profile.knowledge, "Open 10 to 7")
        self.assertTrue(self.profile.approve_before_sending)

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

    def test_preview_shows_each_step_and_stores_nothing(self):
        PostBrain.objects.create(post=self.post, knowledge="Sizes S, M and L in stock")
        response = self.client.post(
            reverse("api-agent-preview", args=[self.account.pk]), {"text": "M size available?", "post": self.post.pk}
        )
        self.assertContains(response, "@customer Sizes S, M and L in stock")
        self.assertContains(response, "Handles it")
        self.assertContains(response, "search_brain")
        self.assertContains(response, "Reply scheduled")
        self.assertContains(response, "nothing was saved or sent")
        self.assertFalse(CommentReply.objects.exists())
        self.assertFalse(AgentRun.objects.exists())
        self.assertEqual(self.graph.calls, [])

    def test_preview_uses_unsaved_brain_text(self):
        url = reverse("api-agent-preview", args=[self.account.pk])
        self.assertContains(self.client.post(url, {"text": "M size available?", "post": ""}), "Asks you first")
        response = self.client.post(url, {"text": "M size available?", "post": "", "knowledge": "Sizes S, M and L in stock"})
        self.assertContains(response, "Handles it")
        self.assertFalse(SellerQuestion.objects.exists())
        self.profile.refresh_from_db()
        self.assertNotIn("Sizes", self.profile.knowledge)

    def test_other_users_get_404(self):
        other = create_user(email="other@example.com", password="a-strong-pass-123")
        self.client.force_login(other)
        reply = CommentReply.objects.create(comment=self.comment(), status=ReplyStatus.PENDING_REVIEW, final_text="hi")
        run = AgentRun.objects.create(account=self.account, comment=self.comment())
        dm = OutgoingDM.objects.create(run=run, kind=DMKind.DM, text="hi", status=ReplyStatus.PENDING_REVIEW)
        question = SellerQuestion.objects.create(run=run, tool_use_id="t", question="?")
        ticket = SupportTicket.objects.create(account=self.account, summary="broken")
        for method, url in [
            ("post", reverse("api-dm-approve", args=[dm.pk])),
            ("post", reverse("api-dm-reject", args=[dm.pk])),
            ("post", reverse("api-question-answer", args=[question.pk])),
            ("post", reverse("api-question-dismiss", args=[question.pk])),
            ("post", reverse("api-ticket-update", args=[ticket.pk])),
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
        from apps.ai.claude import ClaudeError

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
        self.handle(comment)
        self.assertEqual(comment.agent_reply.final_text, "@fan1 Sizes: Sizes S to XL")

        preview = services.preview_run(account=self.account, text="delivery time?", post=self.post)
        self.assertIn("3 to 5 days", preview.trail[-1].input["text"])
        self.assertNotIn("9 days", preview.trail[0].result)

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
        self.handle(follow_up)
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


def claude_response(content: list[dict], stop_reason: str):
    response = mock.Mock(stop_reason=stop_reason)
    response.to_dict.return_value = {"content": content}
    response.usage = mock.Mock(input_tokens=100, cache_read_input_tokens=50, cache_creation_input_tokens=0, output_tokens=20)
    return response


@override_settings(HOOKD_AGENT_MODEL="apps.agent.engines.claude.ClaudeAgentModel", ANTHROPIC_API_KEY="key", AGENT_REPLY_MODEL="cheap-model")
class ClaudeAgentModelTests(AgentTestCase):
    def test_steps_are_recorded_and_thinking_goes_back_unchanged(self):
        from apps.ai.models import AiCall
        from apps.agent.engines.prompts import AGENT_SYSTEM, TOOLS

        first = [
            {"type": "thinking", "thinking": "", "signature": "sig-1"},
            {"type": "tool_use", "id": "tu1", "name": "search_brain", "input": {"topic": "shipping"}},
        ]
        responses = [claude_response(first, "tool_use"), claude_response([{"type": "text", "text": "Answered."}], "end_turn")]
        with mock.patch("apps.ai.claude.anthropic.Anthropic") as client:
            client.return_value.beta.messages.create.side_effect = responses
            comment = self.comment("shipping?")
            self.handle(comment)
        create = client.return_value.beta.messages.create
        second = create.call_args_list[1].kwargs
        self.assertEqual(second["messages"][1], {"role": "assistant", "content": first})
        self.assertEqual(second["messages"][2]["content"][0]["tool_use_id"], "tu1")
        self.assertEqual((second["model"], second["system"], second["tools"]), ("cheap-model", AGENT_SYSTEM, TOOLS))
        self.assertEqual(second["cache_control"], {"type": "ephemeral"})
        self.assertEqual(second["betas"], ["thinking-binding-controls-2026-08-01"])
        self.assertEqual(second["thinking"]["block_binding"], {"prefix_mismatch_behavior": "drop_block"})
        calls_made = AiCall.objects.order_by("pk")
        self.assertEqual(calls_made.count(), 2)
        self.assertEqual(
            (calls_made[0].purpose, calls_made[0].account, calls_made[0].subject, calls_made[0].input_tokens),
            ("comment_agent", self.account, comment, 150),
        )
        self.assertEqual(AgentRun.objects.get().outcome, "Answered.")

    def test_refusal_fails_the_run_and_goes_to_review(self):
        with mock.patch("apps.ai.claude.anthropic.Anthropic") as client:
            client.return_value.beta.messages.create.return_value = claude_response([], "refusal")
            comment = self.comment("price?")
            self.handle(comment)
        self.assertEqual(AgentRun.objects.get().status, RunStatus.FAILED)
        self.assertEqual(comment.agent_reply.status, ReplyStatus.PENDING_REVIEW)

    def test_prompt_and_tools_are_constant_and_match_the_handlers(self):
        from apps.agent.engines.prompts import AGENT_SYSTEM, TOOLS

        self.assertNotIn("{", AGENT_SYSTEM)
        self.assertEqual({tool["name"] for tool in TOOLS}, set(tools.TOOLS))
        self.assertTrue(all(tool["strict"] and not tool["input_schema"]["additionalProperties"] for tool in TOOLS))


class InstagramMessageTests(TestCase):
    def test_dm_text_problems(self):
        provider = InstagramProvider()
        self.assertEqual(provider.dm_text_problems("Hi! Sizes S to L."), [])
        self.assertTrue(provider.dm_text_problems("x" * 1001))
        self.assertTrue(provider.dm_text_problems("  "))


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
        with mock.patch("apps.agent.engines.claude.generate_json", return_value=answer) as call:
            facts = ClaudeBrainLearner().from_caption("Kurta Rs 799")
        self.assertEqual(
            [(fact.scope, fact.label, fact.text) for fact in facts],
            [("post", "Price", "Rs 799"), ("account", "Delivery", "3 to 5 days"), ("post", "Sizes", "S to XL")],
        )
        self.assertEqual(call.call_args.args, ("cheap-model", "Kurta Rs 799"))
        self.assertEqual(call.call_args.kwargs["system"], LEARN_CAPTION_SYSTEM)
        self.assertNotIn("{", LEARN_CAPTION_SYSTEM)


class RunLimitTests(AgentTestCase):
    """Runs always end: step caps, hand-off, crashes, stale workers, question expiry and follow-ups that drop."""

    def started_run(self, text="nice", model=None) -> AgentRun:
        self.handle(self.comment(text), model or ScriptedModel(done()))
        return AgentRun.objects.get()

    @override_settings(AGENT_LOOP_LIMITS={**settings.AGENT_LOOP_LIMITS, "MAX_RUN_STEPS": 3})
    def test_a_long_conversation_is_handed_to_the_seller_and_never_resumes(self):
        model = ScriptedModel(*[call("search_brain", topic="x") for _ in range(3)])
        comment = self.comment("price?")
        self.handle(comment, model)
        run = AgentRun.objects.get()
        self.assertEqual((run.status, run.steps), (RunStatus.HANDED_OFF, 3))
        self.assertIn("handed the conversation to you", run.outcome)
        self.assertEqual(comment.agent_reply.status, ReplyStatus.PENDING_REVIEW)
        Conversation.objects.create(account=self.account, participant_external_id="fan1", last_customer_message_at=timezone.now())
        AgentRun.objects.filter(pk=run.pk).update(conversation=Conversation.objects.get())
        silent = ScriptedModel()
        self.buyer_dm("hello?", model=silent)
        self.assertEqual(silent.seen, [])
        self.assertEqual(AgentRun.objects.count(), 1)

    def test_a_crash_ends_the_run_instead_of_leaving_it_running(self):
        broken = mock.Mock(spec=AgentModel)
        broken.step.side_effect = KeyError("content")
        run = self.started_run(model=broken)
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIsNone(run.wake_at)
        self.assertEqual(AgentEvent.objects.filter(processed_at__isnull=True).count(), 0)

    def test_a_run_whose_worker_died_is_taken_over_later(self):
        run = self.started_run()
        AgentRun.objects.filter(pk=run.pk).update(status=RunStatus.RUNNING, updated_at=timezone.now())
        queries.add_event(run, EventKind.FOLLOWUP, {"note": "x"})
        self.assertEqual(self.run_events(run, ScriptedModel(done())), "busy")
        AgentRun.objects.filter(pk=run.pk).update(updated_at=timezone.now() - timedelta(minutes=11))
        self.assertEqual(self.run_events(run, ScriptedModel(done())), RunStatus.DONE)

    def test_the_seller_is_asked_at_most_three_times(self):
        model = ScriptedModel(call("ask_seller", question="Q?"), done())
        run = self.started_run("fabric?", model)
        for _ in range(2):
            question = SellerQuestion.objects.get(status=QuestionStatus.OPEN)
            model.steps = [call("ask_seller", question="Q?")]
            with self.scripted(model):
                with self.captureOnCommitCallbacks(execute=True):
                    services.answer_question(question, "not sure")
        self.assertEqual(SellerQuestion.objects.count(), 3)
        model.steps = [call("ask_seller", question="Q again?"), done()]
        with self.scripted(model):
            with self.captureOnCommitCallbacks(execute=True):
                services.answer_question(SellerQuestion.objects.get(status=QuestionStatus.OPEN), "no idea")
        self.assertEqual(SellerQuestion.objects.count(), 3)
        self.assertIn("enough", AgentAction.objects.filter(run=run).last().result)

    def test_an_unanswered_question_expires_and_the_run_wraps_up(self):
        model = ScriptedModel(call("ask_seller", question="Fabric?"), done("Told them the seller will get back."))
        run = self.started_run("fabric?", model)
        SellerQuestion.objects.update(created_at=timezone.now() - timedelta(hours=25))
        with self.scripted(model), mock.patch("apps.agent.tasks.send_dm.apply_async"):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("wake_agent_runs", stdout=mock.Mock())
        self.assertIn("did not answer in time", model.last_user_text())
        self.assertEqual(SellerQuestion.objects.get().status, QuestionStatus.EXPIRED)
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.DONE)
        self.assertEqual(self.client.get(reverse("agent-inbox")).context["status"], ReplyStatus.PENDING_REVIEW)

    def test_a_followup_is_dropped_when_the_buyer_writes_back(self):
        run = self.started_run("looks good", ScriptedModel(call("schedule_followup", hours=20, note="nudge"), done()))
        self.assertEqual(run.status, RunStatus.WAITING_FOLLOWUP)
        conversation = Conversation.objects.create(account=self.account, participant_external_id="fan1")
        AgentRun.objects.filter(pk=run.pk).update(conversation=conversation)
        model = ScriptedModel(done("Answered."))
        self.buyer_dm("how much?", model=model)
        self.assertNotIn("Follow-up due", json.dumps(model.seen))
        self.assertEqual(AgentAction.objects.filter(tool="drop_followup").get().result, "Dropped the follow-up: the buyer wrote back.")
        run.refresh_from_db()
        self.assertEqual((run.status, run.wake_at), (RunStatus.DONE, None))

    def test_a_followup_that_can_no_longer_dm_is_dropped_without_calling_the_model(self):
        run = self.started_run("looks good", ScriptedModel(call("schedule_followup", hours=20, note="nudge"), done()))
        AgentRun.objects.filter(pk=run.pk).update(private_reply_used_at=timezone.now())
        AgentEvent.objects.filter(kind=EventKind.FOLLOWUP).update(available_at=timezone.now())
        model = ScriptedModel()
        self.assertEqual(self.run_events(run, model), RunStatus.DONE)
        self.assertEqual(model.seen, [])
        self.assertIn("no longer allows a DM", AgentAction.objects.get(tool="drop_followup").result)

    def test_a_followup_cannot_schedule_another_one(self):
        run = self.started_run("looks good", ScriptedModel(call("schedule_followup", hours=20, note="nudge"), done()))
        AgentEvent.objects.filter(kind=EventKind.FOLLOWUP).update(available_at=timezone.now())
        model = ScriptedModel(call("schedule_followup", hours=5, note="again"), done("Left it."))
        self.assertEqual(self.run_events(run, model), RunStatus.DONE)
        self.assertIn("Do not chase the buyer again", AgentAction.objects.filter(tool="schedule_followup").last().result)
        self.assertIn("You cannot schedule another follow-up", model.first_user_text())

    def test_the_same_problem_is_not_ticketed_twice(self):
        SupportTicket.objects.create(account=self.account, buyer_external_id="fan1", product="Blue kurta", summary="torn")
        self.started_run("my kurta came torn", ScriptedModel(call("create_ticket", product="blue KURTA", summary="torn again"), done()))
        self.assertEqual(SupportTicket.objects.count(), 1)
        self.assertIn("already open", AgentAction.objects.get().result)

    @override_settings(AGENT_LOOP_LIMITS={**settings.AGENT_LOOP_LIMITS, "MAX_RUNS_PER_BUYER_PER_DAY": 2})
    def test_one_person_cannot_start_endless_runs(self):
        for _ in range(2):
            self.handle(self.comment("hi"), ScriptedModel(done()))
        comment = self.comment("hi again")
        self.assertEqual(self.handle(comment), ReplyStatus.SKIPPED)
        self.assertIn("2 comments from this person today", comment.agent_reply.decision["reason"])

    def test_wrong_tool_arguments_are_an_error_but_tool_bugs_are_not_hidden(self):
        run = AgentRun(account=self.account, comment=self.comment(), messages=[])
        ctx = tools.ToolContext(run=run, profile=self.profile, post=self.post, dry_run=True)
        self.assertTrue(tools.execute(ctx, "save_fact", {"label": "x"}).is_error)
        with mock.patch.dict(tools.TOOLS, {"search_brain": mock.Mock(side_effect=TypeError("bug"), __signature__=None)}):
            with self.assertRaises(TypeError):
                tools.execute(ctx, "search_brain", {"topic": "x"})
