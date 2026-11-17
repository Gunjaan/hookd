from django.urls import path

from apps.content import api, views

urlpatterns = [
    path("studio/", views.studio, name="studio"),
    path("studio/accounts/<int:account_id>/", views.account_dashboard, name="studio-account"),
    path("studio/accounts/<int:account_id>/compose/", views.compose, name="compose"),
    path("studio/posts/<int:post_id>/", views.post_detail, name="post-detail"),
    path("studio/publishing/", views.publishing, name="publishing"),
    path("api/accounts/<int:account_id>/posts/", api.account_posts, name="api-account-posts"),
    path("api/accounts/<int:account_id>/posts/refresh/", api.account_posts_refresh, name="api-account-posts-refresh"),
    path("api/posts/<int:post_id>/comments/", api.post_comments, name="api-post-comments"),
    path("api/posts/<int:post_id>/preview/", api.post_preview, name="api-post-preview"),
    path("api/posts/<int:post_id>/insights/refresh/", api.post_insights_refresh, name="api-post-insights-refresh"),
    path("api/publishing/<int:job_id>/check/", api.publish_check, name="api-publish-check"),
    path("publish-media/<uuid:token>/", views.publish_asset, name="publish-asset"),
]
