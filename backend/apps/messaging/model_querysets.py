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
            "shared_post_external_id": incoming.shared_post_external_id,
            "shared_post_caption": incoming.shared_post_caption,
            "sent_at": incoming.sent_at,
            "payload": incoming.payload,
        },
    )


def note_customer_message(conversation: Conversation, sent_at: datetime) -> None:
    """Move the reply window forward, never back (webhooks can arrive out of order)."""
    Conversation.objects.filter(
        Q(last_customer_message_at__isnull=True) | Q(last_customer_message_at__lt=sent_at), pk=conversation.pk
    ).update(last_customer_message_at=sent_at)


def get_conversation(conversation_id: int) -> Conversation | None:
    return Conversation.objects.select_related("account").filter(pk=conversation_id).first()


def get_message(message_id: int) -> DirectMessage | None:
    return DirectMessage.objects.filter(pk=message_id).first()


def latest_shared_post_id(conversation: Conversation) -> str:
    """The post the person shared most recently in this conversation, or empty."""
    return (
        DirectMessage.objects.filter(conversation=conversation, is_from_account=False)
        .exclude(shared_post_external_id="")
        .order_by("-sent_at", "-pk")
        .values_list("shared_post_external_id", flat=True)
        .first()
        or ""
    )


def previous_buyer_message(message: DirectMessage) -> DirectMessage | None:
    """The message the same person sent right before this one in its conversation."""
    return (
        DirectMessage.objects.filter(conversation_id=message.conversation_id, is_from_account=False)
        .filter(Q(sent_at__lt=message.sent_at) | Q(sent_at=message.sent_at, pk__lt=message.pk))
        .order_by("-sent_at", "-pk")
        .first()
    )


def find_conversation(account: SocialAccount, participant_external_id: str) -> Conversation | None:
    return Conversation.objects.filter(account=account, participant_external_id=participant_external_id).first()


def recent_messages(conversation: Conversation, limit: int) -> list[DirectMessage]:
    """The newest messages of a conversation, oldest first."""
    newest = DirectMessage.objects.filter(conversation=conversation).order_by("-sent_at", "-pk")[:limit]
    return list(reversed(newest))
