from django.apps import AppConfig


class TicketsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'tickets'

    def ready(self):
        """
        Register signal handlers when the application is ready.
        Import the signals module to connect signal handlers.
        """
        import tickets.signals  # noqa: F401


