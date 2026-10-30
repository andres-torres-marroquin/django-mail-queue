from django.apps import AppConfig


class MailQueueConfig(AppConfig):
    default_auto_field = 'django.db.models.AutoField'
    name = 'mailqueue'
    label = 'mailqueue'
    verbose_name = "Mail Queue"

    def ready(self):
        from mailqueue import receivers
