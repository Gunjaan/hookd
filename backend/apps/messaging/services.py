"""Direct messages received from platforms: DMs, story replies and story mentions.

Messages are stored per conversation and each conversation tracks when the person last wrote, which opens Meta's 24
hour reply window. Every new message from a person sends `customer_message_received`; the reply agent answers it.
"""

import logging

from apps.integrations import model_querysets as account_queries
from apps.integrations.providers.types import IncomingMessage
from apps.messaging import model_querysets as queries
from apps.messaging.models import Conversation
from apps.messaging.signals import customer_message_received

logger = logging.getLogger(__name__)


def record_incoming_messages(*, platform: str, messages: list[IncomingMessage]) -> int:
    """Store new messages for connected accounts. Duplicates and unknown accounts are skipped.
    Returns the number of messages created."""
    created_count = 0
    for incoming in messages:
        account = account_queries.find_account(platform, incoming.account_external_id)
        if account is None:
            logger.info("Skipping %s message %s for unknown account %s", platform, incoming.external_id, incoming.account_external_id)
            continue
        conversation = queries.get_or_create_conversation(account, incoming.participant_external_id)
        message, created = queries.create_message_if_new(conversation, incoming)
        if created and not incoming.is_from_account:
            if incoming.sent_at:
                queries.note_customer_message(conversation, incoming.sent_at)
            customer_message_received.send(sender=Conversation, conversation=conversation, message=message)
        created_count += int(created)
    return created_count
