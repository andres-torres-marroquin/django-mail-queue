

from django.db import transaction
from django.db.models.signals import pre_save, post_delete, post_save
from django.dispatch import receiver
from django.conf import settings

from mailqueue.models import MailerMessage, Attachment
from . import defaults


@receiver(post_save, sender=MailerMessage)
def send_post_save(sender, instance, signal, *args, **kwargs):
    if getattr(instance, "do_not_send", False):
        instance.do_not_send = False
        return

    if not getattr(settings, 'MAILQUEUE_QUEUE_UP', defaults.MAILQUEUE_QUEUE_UP):
        # If mail queue up is set, wait for the cron or management command
        # to send any email.
        transaction.on_commit(instance.send_mail)


@receiver(pre_save, sender=Attachment)
def delete_old_file(sender, instance, **kwargs):
    if not instance.pk:
        return False
    try:
        old_file = Attachment.objects.get(pk=instance.pk).file_attachment
    except Attachment.DoesNotExist:
        return False
    new_file = instance.file_attachment
    if not old_file == new_file:
        transaction.on_commit(lambda: old_file.delete(save=False))


@receiver(post_delete, sender=Attachment)
def delete_file_from_filesystem(sender, instance, **kwargs):
    if instance.file_attachment:
        file = instance.file_attachment
        transaction.on_commit(lambda: file.delete(save=False))
