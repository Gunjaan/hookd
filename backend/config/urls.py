"""Root URLs: server rendered pages, the health check and the Django admin."""

from django.contrib import admin
from django.urls import include, path

from apps.core.views import health, landing

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health, name='health'),
    path('', landing, name='home'),
    path('', include('apps.accounts.urls')),
    path('', include('apps.integrations.urls')),
    path('', include('apps.content.urls')),
    path('', include('apps.agent.urls')),
]
