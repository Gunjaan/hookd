"""Signal receivers: a post Hookd publishes teaches the agent what its caption says."""

from django.db import transaction
from django.dispatch import receiver

from apps.agent.tasks import learn_from_post
from apps.content.signals import post_published


@receiver(post_published, dispatch_uid="agent-learn-from-published-caption")
def learn_from_published_caption(sender, job, post, **kwargs) -> None:
    transaction.on_commit(lambda: learn_from_post.delay(post.pk))
