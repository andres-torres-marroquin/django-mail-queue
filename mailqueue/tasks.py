from __future__ import absolute_import

from celery import shared_task

from .models import MailerMessage


@shared_task(bind=True, name="tasks.send_mail", default_retry_delay=5, max_retries=5)
def send_mail(self, pk):
    """
    Send a single mailqueue message.
    """
    message = MailerMessage.objects.filter(pk=pk).first()

    if not message:
        # We didn't find the message, nothing to do.
        return

    try:
        message._send(raise_errors=True)
    except Exception as exc:
        raise self.retry(args=[message.pk], exc=exc)



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
