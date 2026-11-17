"""All database access for the messaging app lives here."""

from datetime import datetime

from django.db.models import Q

from apps.integrations.models import SocialAccount
from apps.integrations.providers.types import IncomingMessage
from apps.messaging.models import Conversation, DirectMessage


def get_or_create_conversation(account: SocialAccount, participant_external_id: str) -> Conversation:
    conversation, _ = Conversation.objects.get_or_create(account=account, participant_external_id=participant_external_id)
    return conversation


def create_message_if_new(conversation: Conversation, incoming: IncomingMessage) -> tuple[DirectMessage, bool]:
    return DirectMessage.objects.get_or_create(
        conversation=conversation,
        external_id=incoming.external_id,
        defaults={
            "is_from_account": incoming.is_from_account,
            "kind": incoming.kind,
            "text": incoming.text,
            "story_external_id": incoming.story_external_id,
            "media_url": incoming.media_url,
            "reply_to_external_id": incoming.reply_to_external_id,
            "sent_at": incoming.sent_at,
            "payload": incoming.payload,
        },
    )


def note_customer_message(conversation: Conversation, sent_at: datetime) -> None:
    """Move the reply window forward, never back (webhooks can arrive out of order)."""
    Conversation.objects.filter(
        Q(last_customer_message_at__isnull=True) | Q(last_customer_message_at__lt=sent_at), pk=conversation.pk
    ).update(last_customer_message_at=sent_at)
