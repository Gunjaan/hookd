import hashlib
import hmac
import json

from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.model_querysets import create_user
from apps.comments.tests import ACCOUNT_ID, INSTAGRAM_TEST_SETTINGS
from apps.integrations import model_querysets as account_queries
from apps.integrations.providers.types import AccountProfile, TokenBundle
from apps.messaging.models import Conversation, DirectMessage

CUSTOMER_ID = "5550001"


def messaging_payload(*events, account_id=ACCOUNT_ID):
    return {"object": "instagram", "entry": [{"id": account_id, "time": 1763208000, "messaging": list(events)}]}


def message_event(message, *, sender=CUSTOMER_ID, recipient=ACCOUNT_ID, timestamp=1763208000000):
    return {"sender": {"id": sender}, "recipient": {"id": recipient}, "timestamp": timestamp, "message": message}


@override_settings(INSTAGRAM=INSTAGRAM_TEST_SETTINGS)
class InstagramMessageWebhookTests(TestCase):
    def setUp(self):
        user = create_user(email="creator@example.com", password="a-strong-pass-123")
        self.account = account_queries.upsert_account(
            user=user,
            platform="instagram",
            profile=AccountProfile(external_id=ACCOUNT_ID, username="asha.edits"),
            tokens=TokenBundle(access_token="suat"),
        )

    def deliver(self, payload):
        body = json.dumps(payload).encode()
        signature = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
        return self.client.post(
            reverse("webhook", args=["instagram"]), body, content_type="application/json", HTTP_X_HUB_SIGNATURE_256=signature
        )

    def test_dm_is_stored_once_and_opens_the_reply_window(self):
        payload = messaging_payload(message_event({"mid": "mid1", "text": "Price?"}))
        self.assertEqual(self.deliver(payload).status_code, 200)
        self.deliver(payload)
        message = DirectMessage.objects.get()
        self.assertEqual((message.kind, message.text, message.is_from_account), ("text", "Price?", False))
        conversation = message.conversation
        self.assertEqual((conversation.account, conversation.participant_external_id), (self.account, CUSTOMER_ID))
        self.assertEqual(conversation.last_customer_message_at.year, 2025)
        self.assertEqual((conversation.reply_window_ends_at - conversation.last_customer_message_at).total_seconds(), 86400)

    def test_story_reply_and_story_mention(self):
        self.deliver(messaging_payload(
            message_event({"mid": "mid1", "text": "Want this!", "reply_to": {"story": {"id": "s1", "url": "https://cdn/s1"}}}),
            message_event({"mid": "mid2", "attachments": [{"type": "story_mention", "payload": {"url": "https://cdn/m"}}]}),
            message_event({"mid": "mid3", "attachments": [{"type": "image", "payload": {"url": "https://cdn/i"}}]}),
        ))
        reply, mention, media = DirectMessage.objects.order_by("external_id")
        self.assertEqual((reply.kind, reply.story_external_id, reply.media_url), ("story_reply", "s1", "https://cdn/s1"))
        self.assertEqual((mention.kind, mention.media_url), ("story_mention", "https://cdn/m"))
        self.assertEqual(media.kind, "media")
        self.assertEqual(Conversation.objects.count(), 1)

    def test_shared_post_and_reel_keep_their_id_and_caption(self):
        post = {"ig_post_media_id": "1801", "title": "Your first coffee deserves a mug", "url": "https://cdn/p"}
        self.deliver(messaging_payload(
            message_event({"mid": "mid1", "attachments": [{"type": "share", "payload": post}, {"type": "ig_post", "payload": post}]}),
            message_event({"mid": "mid2", "attachments": [{"type": "ig_reel", "payload": {"reel_video_id": "1802", "title": "Reel"}}]}),
        ))
        shared, reel = DirectMessage.objects.order_by("external_id")
        self.assertEqual(
            (shared.kind, shared.shared_post_external_id, shared.shared_post_caption, shared.media_url),
            ("share", "1801", "Your first coffee deserves a mug", "https://cdn/p"),
        )
        self.assertEqual((reel.kind, reel.shared_post_external_id, reel.shared_post_caption), ("share", "1802", "Reel"))

    def test_echo_is_flagged_and_does_not_move_the_reply_window(self):
        self.deliver(messaging_payload(
            message_event({"mid": "mid1", "text": "hi"}, timestamp=1763208000000),
            message_event({"mid": "mid2", "text": "Hello!", "is_echo": True}, sender=ACCOUNT_ID, recipient=CUSTOMER_ID, timestamp=1763209000000),
        ))
        echo = DirectMessage.objects.get(external_id="mid2")
        self.assertTrue(echo.is_from_account)
        self.assertEqual(echo.conversation.participant_external_id, CUSTOMER_ID)
        self.assertEqual(echo.conversation.last_customer_message_at.timestamp(), 1763208000)

    def test_out_of_order_delivery_keeps_the_latest_time(self):
        self.deliver(messaging_payload(message_event({"mid": "mid2", "text": "later"}, timestamp=1763209000000)))
        self.deliver(messaging_payload(message_event({"mid": "mid1", "text": "earlier"}, timestamp=1763208000000)))
        self.assertEqual(Conversation.objects.get().last_customer_message_at.timestamp(), 1763209000)

    def test_deleted_messages_other_events_and_unknown_accounts_are_skipped(self):
        self.deliver(messaging_payload(
            message_event({"mid": "mid1", "is_deleted": True}),
            {"sender": {"id": CUSTOMER_ID}, "recipient": {"id": ACCOUNT_ID}, "timestamp": 1, "read": {"mid": "mid0"}},
        ))
        self.assertEqual(self.deliver(messaging_payload(message_event({"mid": "mid9"}), account_id="1")).status_code, 200)
        self.assertFalse(DirectMessage.objects.exists())
