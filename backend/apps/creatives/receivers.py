"""Signal receivers for the post studio."""

from django.dispatch import receiver

from apps.content.signals import post_published
from apps.creatives import services


@receiver(post_published, dispatch_uid="creatives-save-brain-answers")
def save_brain_answers(sender, job, post, **kwargs):
    services.save_brain_answers_for_job(job, post)
