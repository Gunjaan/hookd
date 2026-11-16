"""Business logic for comments received from platforms."""

import logging

from django.db import transaction

from apps.agent.tasks import handle_comment
from apps.comments import model_querysets as queries
from apps.integrations import model_querysets as account_queries
from apps.integrations.providers.types import IncomingComment

logger = logging.getLogger(__name__)


def record_incoming_comments(*, platform: str, comments: list[IncomingComment]) -> int:
    """Store new comments for connected accounts. Duplicates and unknown accounts are skipped.

    Each new comment not written by the account itself is handed to the reply agent once the transaction commits.
    Returns the number of comments created.
    """
    created_count = 0
    for incoming in comments:
        account = account_queries.find_account(platform, incoming.account_external_id)
        if account is None:
            logger.info("Skipping %s comment %s for unknown account %s", platform, incoming.external_id, incoming.account_external_id)
            continue
        comment, created = queries.create_comment_if_new(account=account, incoming=incoming)
        if created and not comment.is_from_account:
            transaction.on_commit(lambda comment_id=comment.pk: handle_comment.delay(comment_id))
        created_count += int(created)
    return created_count
