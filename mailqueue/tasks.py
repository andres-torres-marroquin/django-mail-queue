from __future__ import absolute_import

from celery import shared_task

from .models import MailerMessage


@shared_task(name="tasks.send_mail", default_retry_delay=5, max_retries=5)
def send_mail(pk):
    """
    Send a single mailqueue message.
    """
    message = MailerMessage.objects.filter(pk=pk).first()

    if not message:
        # We didn't find the message, nothing to do.
        return

    message._send()

    # Retry when message is not sent
    if not message.sent:
        send_mail.retry([message.pk])


@shared_task()
def clear_sent_messages():
    from mailqueue.models import MailerMessage
    MailerMessage.objects.clear_sent_messages()


@shared_task
def send_queued_messages():
    """
    This task sends the mailqueue messages waiting to be sent.
    """

    MailerMessage.objects.send_queued()
