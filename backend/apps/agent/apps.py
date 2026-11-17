from django.apps import AppConfig


class AgentConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.agent'

    def ready(self):
        from apps.agent import receivers  # noqa: F401  connects the signal receivers
