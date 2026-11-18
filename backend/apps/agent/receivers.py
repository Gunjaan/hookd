"""Signal receivers: a post Hookd publishes teaches the agent what its caption says, and every buyer DM goes to the
agent."""

from django.db import transaction
from django.dispatch import receiver

from apps.agent.tasks import handle_buyer_message, learn_from_post
from apps.content.signals import post_published
from apps.messaging.signals import customer_message_received


@receiver(post_published, dispatch_uid="agent-learn-from-published-caption")
def learn_from_published_caption(sender, job, post, **kwargs) -> None:
    transaction.on_commit(lambda: learn_from_post.delay(post.pk))


@receiver(customer_message_received, dispatch_uid="agent-handle-buyer-message")
def answer_buyer_message(sender, conversation, message, **kwargs) -> None:
    transaction.on_commit(lambda: handle_buyer_message.delay(conversation.pk, message.pk))
