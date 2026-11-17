from django.urls import path

from apps.creatives import api, views

urlpatterns = [
    path("studio/publishing/new/", views.creative_new, name="creative-new"),
    path("studio/publishing/drafts/<int:creative_id>/", views.creative_detail, name="creative-detail"),
    path("studio/publishing/drafts/<int:creative_id>/publish/", views.creative_publish, name="creative-publish"),
    path("studio/publishing/drafts/<int:creative_id>/delete/", views.creative_delete, name="creative-delete"),
    path("studio/publishing/files/<str:kind>/<int:file_id>/", views.creative_file, name="creative-file"),
    path("api/drafts/<int:creative_id>/progress/", api.draft_progress, name="api-draft-progress"),
    path("api/drafts/<int:creative_id>/copy/", api.draft_copy, name="api-draft-copy"),
    path("api/drafts/<int:creative_id>/rewrite/", api.draft_rewrite, name="api-draft-rewrite"),
    path("api/slides/<int:slide_id>/redo/", api.slide_redo, name="api-slide-redo"),
    path("api/versions/<int:version_id>/use/", api.version_use, name="api-version-use"),
]
