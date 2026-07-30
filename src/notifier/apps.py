from django.apps import AppConfig


class NotifierConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "notifier"
    verbose_name = "Notifications"

    def ready(self) -> None:
        # Connects the post_save receiver that turns status="ready" into a
        # send. Imported here, never at module scope, so the app loads before
        # the models it depends on.
        #
        # Deliberately does NOT import admin: notifier must stay installable
        # with INSTALLED_APPS = ["notifier"] and no django.contrib.admin
        # (PRD 2.10 / AC-35). Django's admin autodiscovery loads admin.py when
        # the admin app is present.
        from notifier import checks, receivers  # noqa: F401
