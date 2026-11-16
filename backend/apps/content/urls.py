from django.urls import path

from apps.content import views

urlpatterns = [
    path("studio/", views.studio, name="studio"),
    path("studio/accounts/<int:account_id>/", views.account_dashboard, name="studio-account"),
    path("studio/accounts/<int:account_id>/refresh/", views.account_refresh, name="studio-refresh"),
    path("studio/accounts/<int:account_id>/compose/", views.compose, name="compose"),
    path("studio/posts/<int:post_id>/", views.post_detail, name="post-detail"),
    path("studio/posts/<int:post_id>/insights/refresh/", views.post_insights_refresh, name="post-insights-refresh"),
    path("studio/posts/<int:post_id>/comments/", views.post_comments, name="post-comments"),
    path("studio/publishing/", views.publishing, name="publishing"),
    path("studio/publishing/<int:job_id>/check/", views.publish_check, name="publish-check"),
    path("publish-media/<uuid:token>/", views.publish_asset, name="publish-asset"),
]
