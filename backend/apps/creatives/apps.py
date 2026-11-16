from django.apps import AppConfig


class CreativesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.creatives'

    def ready(self):
        from apps.creatives import receivers  # noqa: F401  connects the signal receivers
