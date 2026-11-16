"""All database access for the content app lives here."""

from datetime import datetime

from django.core.files.uploadedfile import UploadedFile
from django.db.models import QuerySet
from django.utils import timezone

from apps.content.models import Post, PublishAsset, PublishJob
from apps.integrations.models import SocialAccount
from apps.integrations.providers.types import MediaItem, Metric


def upsert_post(account: SocialAccount, item: MediaItem) -> Post:
    post, _ = Post.objects.update_or_create(
        account=account,
        external_id=item.external_id,
        defaults={
            "media_type": item.media_type,
            "product_type": item.product_type,
            "caption": item.caption,
            "permalink": item.permalink,
            "media_url": item.media_url,
            "thumbnail_url": item.thumbnail_url,
            "posted_at": item.posted_at,
            "like_count": item.like_count,
            "comments_count": item.comments_count,
            "synced_at": timezone.now(),
        },
    )
    return post


def posts_in_order(account: SocialAccount, external_ids: list[str]) -> list[Post]:
    by_id = {post.external_id: post for post in Post.objects.filter(account=account, external_id__in=external_ids)}
    return [by_id[external_id] for external_id in external_ids if external_id in by_id]


def delete_posts_missing_since(account: SocialAccount, *, since: datetime, keep_external_ids: list[str]) -> int:
    """Remove cached posts newer than `since` that the platform no longer returns (deleted on the platform)."""
    deleted, _ = (
        Post.objects.filter(account=account, posted_at__gte=since).exclude(external_id__in=keep_external_ids).delete()
    )
    return deleted


def get_post_for_user(user, post_id: int) -> Post | None:
    return Post.objects.select_related("account").filter(account__user=user, pk=post_id).first()


def save_post_insights(post: Post, metrics: list[Metric]) -> Post:
    post.insights = [
        {"key": metric.key, "label": metric.label, "value": metric.value, "unit": metric.unit} for metric in metrics
    ]
    post.insights_fetched_at = timezone.now()
    post.save(update_fields=["insights", "insights_fetched_at", "updated_at"])
    return post


def create_publish_job(*, account: SocialAccount, kind: str, caption: str) -> PublishJob:
    return PublishJob.objects.create(account=account, kind=kind, caption=caption)


def create_publish_asset(*, job: PublishJob, position: int, upload: UploadedFile) -> PublishAsset:
    return PublishAsset.objects.create(
        job=job, position=position, file=upload, content_type=upload.content_type or "application/octet-stream"
    )


def set_publish_id(job: PublishJob, publish_id: str) -> PublishJob:
    job.publish_id = publish_id
    job.save(update_fields=["publish_id", "updated_at"])
    return job


def mark_publish_job_published(job: PublishJob, post: Post) -> PublishJob:
    job.status = PublishJob.Status.PUBLISHED
    job.post = post
    job.error = ""
    job.published_at = timezone.now()
    job.save(update_fields=["status", "post", "error", "published_at", "updated_at"])
    return job


def mark_publish_job_failed(job: PublishJob, error: str) -> PublishJob:
    job.status = PublishJob.Status.FAILED
    job.error = error[:1000]
    job.save(update_fields=["status", "error", "updated_at"])
    return job


def delete_publish_assets(job: PublishJob) -> None:
    """Delete the uploaded files and their rows once the platform no longer needs them."""
    for asset in PublishAsset.objects.filter(job=job):
        asset.file.delete(save=False)
        asset.delete()


def list_publish_assets(job: PublishJob) -> list[PublishAsset]:
    return list(PublishAsset.objects.filter(job=job))


def processing_publish_jobs() -> QuerySet[PublishJob]:
    return PublishJob.objects.select_related("account").filter(status=PublishJob.Status.PROCESSING).order_by("pk")


def list_publish_jobs_for_user(user) -> QuerySet[PublishJob]:
    return PublishJob.objects.select_related("account", "post").filter(account__user=user)


def get_publish_job(job_id: int) -> PublishJob | None:
    return PublishJob.objects.select_related("account").filter(pk=job_id).first()


def get_publish_job_for_user(user, job_id: int) -> PublishJob | None:
    return PublishJob.objects.select_related("account").filter(account__user=user, pk=job_id).first()


def find_servable_asset(token) -> PublishAsset | None:
    return PublishAsset.objects.filter(token=token, job__status=PublishJob.Status.PROCESSING).first()


def find_post(account: SocialAccount, external_id: str) -> Post | None:
    return Post.objects.filter(account=account, external_id=external_id).first() if external_id else None


def latest_posts(account: SocialAccount, limit: int) -> list[Post]:
    return list(Post.objects.filter(account=account).order_by("-posted_at")[:limit])
