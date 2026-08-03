from django.apps import AppConfig


class HubConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "hub"
    verbose_name = "SMTI Hub"

    def ready(self):
        from hub import signals  # registers the auth audit receivers

        signals.connect_axes()
