from django.urls import path

from apps.creatives import views

urlpatterns = [
    path("studio/publishing/new/", views.creative_new, name="creative-new"),
    path("studio/publishing/drafts/<int:creative_id>/", views.creative_detail, name="creative-detail"),
    path("studio/publishing/drafts/<int:creative_id>/copy/", views.creative_copy, name="creative-copy"),
    path("studio/publishing/drafts/<int:creative_id>/rewrite/", views.creative_rewrite, name="creative-rewrite"),
    path("studio/publishing/drafts/<int:creative_id>/publish/", views.creative_publish, name="creative-publish"),
    path("studio/publishing/drafts/<int:creative_id>/delete/", views.creative_delete, name="creative-delete"),
    path("studio/publishing/slides/<int:slide_id>/redo/", views.creative_slide_redo, name="creative-slide-redo"),
    path("studio/publishing/files/<str:kind>/<int:file_id>/", views.creative_file, name="creative-file"),
]
