"""Business logic for the creator studio: recent posts, insights, comments and publishing.

Loading is lazy on purpose. The dashboard asks the platform for one page of
posts (counts only), insights load when a post is opened, and comments load
one page at a time when the creator asks for them.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta

from django.conf import settings
from django.core.cache import cache
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from apps.comments import model_querysets as comment_queries
from apps.content import model_querysets as queries
from apps.content.exceptions import PostNotFound, PostRemoved, PublishingUnavailable, PublishJobNotFound, PublishQuotaReached
from apps.content.models import Post, PublishAsset, PublishJob
from apps.content.signals import post_published
from apps.integrations import model_querysets as account_queries
from apps.integrations.constants import Capability, PublishState
from apps.integrations.exceptions import FeatureNotSupported, ProviderAPIError, TokenExpiredError
from apps.integrations.models import SocialAccount
from apps.integrations.providers.registry import get_provider
from apps.integrations.providers.types import IncomingComment, Metric

logger = logging.getLogger(__name__)

POSTS_PAGE_SIZE = 10
COMMENTS_PAGE_SIZE = 25
RECENT_POSTS_TTL = timedelta(minutes=5)
INSIGHTS_TTL = timedelta(hours=1)
ACCOUNT_INSIGHTS_DAYS = 7
# Instagram containers expire after 24 hours; anything still processing after this is given up.
PUBLISH_TIMEOUT = timedelta(hours=1)
PUBLISH_POLL_INTERVAL = timedelta(seconds=20)


@contextmanager
def _platform_call(account: SocialAccount) -> Iterator[None]:
    """Mark the account expired when the platform rejects its token, then re-raise."""
    try:
        yield
    except TokenExpiredError as exc:
        account_queries.set_account_status(account, SocialAccount.Status.EXPIRED, exc.message)
        raise


def _require_capability(account: SocialAccount, capability: Capability):
    provider = get_provider(account.platform)
    if capability not in provider.capabilities:
        raise FeatureNotSupported(f"{provider.display_name} does not support this yet.")
    return provider


# Accounts and posts


def studio_accounts(user) -> list[SocialAccount]:
    return list(account_queries.list_accounts_for_user(user))


def get_user_post(*, user, post_id: int) -> Post:
    post = queries.get_post_for_user(user, post_id)
    if post is None:
        raise PostNotFound()
    return post


@dataclass(frozen=True)
class PostPage:
    posts: list[Post]
    next_cursor: str = ""


def _recent_posts_key(account: SocialAccount) -> str:
    return f"content:recent-posts:{account.pk}"


def recent_posts(account: SocialAccount, *, force: bool = False) -> PostPage:
    """The newest page of posts. Served from the cache for RECENT_POSTS_TTL unless `force`."""
    cached = None if force else cache.get(_recent_posts_key(account))
    if cached is not None:
        return PostPage(posts=queries.posts_in_order(account, cached["ids"]), next_cursor=cached["next"])

    page = _fetch_posts(account, after="")
    if page.posts:
        oldest = min((post.posted_at for post in page.posts if post.posted_at), default=None)
        if oldest:
            queries.mark_posts_missing_since(
                account, since=oldest, keep_external_ids=[post.external_id for post in page.posts]
            )
    cache.set(
        _recent_posts_key(account),
        {"ids": [post.external_id for post in page.posts], "next": page.next_cursor},
        RECENT_POSTS_TTL.total_seconds(),
    )
    return page


def older_posts(account: SocialAccount, *, after: str) -> PostPage:
    """The page of posts after the given cursor, always fetched live."""
    return _fetch_posts(account, after=after)


def ensure_post(account: SocialAccount, external_id: str) -> Post | None:
    """The stored post, fetched from the platform first when we have not seen it (posted outside Hookd).

    Instagram sends no webhook for new posts, so a comment on an unknown post is how we learn about it.
    A failed fetch is logged and returns None so callers carry on without the post.
    """
    if not external_id:
        return None
    post = queries.find_post(account, external_id)
    if post is not None:
        return post
    provider = get_provider(account.platform)
    try:
        with _platform_call(account):
            item = provider.get_media(account, external_id)
    except (ProviderAPIError, FeatureNotSupported) as exc:
        logger.warning("Could not fetch %s post %s for account %s: %s", account.platform, external_id, account.pk, exc.message)
        return None
    post = queries.upsert_post(account, item)
    cache.delete(_recent_posts_key(account))
    return post


def _fetch_posts(account: SocialAccount, *, after: str) -> PostPage:
    provider = get_provider(account.platform)
    with _platform_call(account):
        media = provider.list_media(account, limit=POSTS_PAGE_SIZE, after=after)
    return PostPage(posts=[queries.upsert_post(account, item) for item in media.items], next_cursor=media.next_cursor)


# Insights


def account_insights(account: SocialAccount) -> list[Metric]:
    """Totals for the last ACCOUNT_INSIGHTS_DAYS days, cached for INSIGHTS_TTL. Empty without insights support."""
    provider = get_provider(account.platform)
    if Capability.INSIGHTS not in provider.capabilities:
        return []
    key = f"content:account-insights:{account.pk}"
    metrics = cache.get(key)
    if metrics is None:
        with _platform_call(account):
            metrics = provider.fetch_account_insights(account, days=ACCOUNT_INSIGHTS_DAYS)
        cache.set(key, metrics, INSIGHTS_TTL.total_seconds())
    return metrics


def post_insights(post: Post, *, force: bool = False) -> Post:
    """Fetch the post's metrics when they are missing, older than INSIGHTS_TTL, or `force`."""
    provider = get_provider(post.account.platform)
    fresh = post.insights_fetched_at and timezone.now() - post.insights_fetched_at < INSIGHTS_TTL
    if (fresh and not force) or post.removed_at or Capability.INSIGHTS not in provider.capabilities:
        return post
    with _platform_call(post.account):
        metrics = provider.fetch_media_insights(post.account, post.external_id, post.product_type)
    return queries.save_post_insights(post, metrics)


# Comments


@dataclass(frozen=True)
class CommentList:
    comments: list[IncomingComment]
    next_cursor: str = ""


def post_comments(post: Post, *, after: str = "") -> CommentList:
    """One page of comments, fetched live. New ones are stored so the agent has the history."""
    if post.removed_at:
        raise PostRemoved()
    provider = get_provider(post.account.platform)
    with _platform_call(post.account):
        page = provider.list_comments(post.account, post.external_id, limit=COMMENTS_PAGE_SIZE, after=after)
    for incoming in page.comments:
        comment_queries.create_comment_if_new(account=post.account, incoming=incoming)
    return CommentList(comments=page.comments, next_cursor=page.next_cursor)


# Publishing


def publish_asset_url(asset: PublishAsset) -> str:
    return f"{settings.APP_URL}{reverse('publish-asset', args=[asset.token])}"


def can_publish(account: SocialAccount) -> bool:
    return Capability.PUBLISH in get_provider(account.platform).capabilities


def remaining_publish_quota(account: SocialAccount) -> int:
    provider = _require_capability(account, Capability.PUBLISH)
    with _platform_call(account):
        return provider.publishing_quota(account).remaining


def create_publish_job(*, account: SocialAccount, kind: str, caption: str, uploads: list[UploadedFile]) -> PublishJob:
    """Store the uploads, hand them to the platform and try to publish right away.

    Images are usually ready at once. Videos keep the job "processing" and a
    Celery task polls the platform until it finishes.
    """
    provider = _require_capability(account, Capability.PUBLISH)
    if not settings.APP_URL.startswith("https://"):
        raise PublishingUnavailable()
    if remaining_publish_quota(account) <= 0:
        raise PublishQuotaReached()

    job = queries.create_publish_job(account=account, kind=kind, caption=caption)
    assets = [queries.create_publish_asset(job=job, position=index, upload=upload) for index, upload in enumerate(uploads)]
    try:
        with _platform_call(account):
            publish_id = provider.start_publish(
                account, kind=kind, media_urls=[publish_asset_url(asset) for asset in assets], caption=caption
            )
    except ProviderAPIError as exc:
        _fail_job(job, exc.message)
        return job
    queries.set_publish_id(job, publish_id)
    job = advance_publish_job(job)
    if job.status == PublishJob.Status.PROCESSING:
        schedule_publish_poll(job)
    return job


def schedule_publish_poll(job: PublishJob) -> None:
    """Queue a status check PUBLISH_POLL_INTERVAL from now, once the current transaction commits."""
    from apps.content.tasks import poll_publish_job  # tasks.py imports this module

    transaction.on_commit(
        lambda: poll_publish_job.apply_async(args=[job.pk], countdown=PUBLISH_POLL_INTERVAL.total_seconds())
    )


def poll_publish_job(job_id: int) -> str:
    """Celery entry point: advance the job and check again later while it is still processing.

    Returns the job status ("missing" when the job was deleted).
    """
    job = queries.get_publish_job(job_id)
    if job is None:
        return "missing"
    job = advance_publish_job(job)
    if job.status == PublishJob.Status.PROCESSING:
        schedule_publish_poll(job)
    return job.status


def advance_publish_job(job: PublishJob) -> PublishJob:
    """Check a processing job on the platform and publish it once the upload is ready."""
    if job.status != PublishJob.Status.PROCESSING:
        return job
    provider = get_provider(job.account.platform)
    try:
        with _platform_call(job.account):
            status = provider.publish_status(job.account, job.publish_id)
            if status.state == PublishState.FAILED:
                return _fail_job(job, status.detail or "The platform could not process this upload.")
            if status.state == PublishState.IN_PROGRESS:
                if timezone.now() - job.created_at > PUBLISH_TIMEOUT:
                    return _fail_job(job, "The platform took too long to process this upload.")
                return job
            item = provider.finish_publish(job.account, job.publish_id)
    except ProviderAPIError as exc:
        return _fail_job(job, exc.message)

    post = queries.upsert_post(job.account, item)
    queries.delete_publish_assets(job)
    cache.delete(_recent_posts_key(job.account))
    logger.info("Published %s job %s as %s", job.account.platform, job.pk, item.external_id)
    job = queries.mark_publish_job_published(job, post)
    post_published.send(sender=PublishJob, job=job, post=post)
    return job


def _fail_job(job: PublishJob, error: str) -> PublishJob:
    logger.warning("Publish job %s failed: %s", job.pk, error)
    queries.delete_publish_assets(job)
    return queries.mark_publish_job_failed(job, error)


@dataclass
class PublishSummary:
    published: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    processing: list[int] = field(default_factory=list)


def advance_processing_jobs() -> PublishSummary:
    summary = PublishSummary()
    for job in queries.processing_publish_jobs():
        job = advance_publish_job(job)
        {
            PublishJob.Status.PUBLISHED: summary.published,
            PublishJob.Status.FAILED: summary.failed,
            PublishJob.Status.PROCESSING: summary.processing,
        }[job.status].append(job.pk)
    return summary


def list_publish_jobs(user):
    return queries.list_publish_jobs_for_user(user)


def get_user_publish_job(*, user, job_id: int) -> PublishJob:
    job = queries.get_publish_job_for_user(user, job_id)
    if job is None:
        raise PublishJobNotFound()
    return job


def find_servable_asset(token) -> PublishAsset | None:
    return queries.find_servable_asset(token)
