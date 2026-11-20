"""Root URLs: server rendered pages, the health check, the ANS agent card, the contact form and the Django admin."""

from django.contrib import admin
from django.urls import include, path

from apps.agent.views import agent_card
from apps.core.api import contact
from apps.core.views import health, landing

urlpatterns = [
    path('admin/', admin.site.urls),
    path('health/', health, name='health'),
    path('.well-known/agent-card.json', agent_card, name='agent-card'),
    path('', landing, name='home'),
    path('api/contact/', contact, name='api-contact'),
    path('', include('apps.accounts.urls')),
    path('', include('apps.integrations.urls')),
    path('', include('apps.content.urls')),
    path('', include('apps.agent.urls')),
    path('', include('apps.creatives.urls')),
]
