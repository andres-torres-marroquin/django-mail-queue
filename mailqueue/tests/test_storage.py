from unittest.mock import patch

from django.core import mail
from django.core.files.base import ContentFile
from django.core.files.storage import Storage
from django.db import transaction
from django.test import TransactionTestCase, override_settings

from mailqueue.models import Attachment, MailerMessage


class RemoteStorage(Storage):
    def __init__(self):
        self.files = {}

    def _open(self, name, mode='rb'):
        if name not in self.files:
            raise FileNotFoundError(name)
        return ContentFile(self.files[name], name=name)

    def _save(self, name, content):
        self.files[name] = content.read()
        return name

    def exists(self, name):
        return name in self.files

    def delete(self, name):
        self.files.pop(name, None)

    def path(self, name):
        raise NotImplementedError('No local paths')


@override_settings(MAILQUEUE_CELERY=False)
class StorageTests(TransactionTestCase):
    def setUp(self):
        self.storage = RemoteStorage()
        self.addCleanup(patch.stopall)
        patch.object(Attachment._meta.get_field('file_attachment'), 'storage', self.storage).start()
        self.message = MailerMessage(to_address='recipient@example.com', subject='Attachment', content='Body')
        self.message._save_without_sending()

    def attach(self, name='report.txt', content=b'report'):
        attachment = Attachment(email=self.message, original_filename=name)
        attachment.file_attachment.save(name, ContentFile(content), save=False)
        attachment.save()
        return attachment

    def test_remote_attachments_send_and_delete(self):
        first = self.attach()
        self.attach('other.txt', b'other')
        self.message._send()
        self.assertTrue(self.message.sent)
        self.assertEqual([(a[0], a[1]) for a in mail.outbox[0].attachments], [('report.txt', 'report'), ('other.txt', 'other')])
        name = first.file_attachment.name
        first.delete()
        self.assertFalse(self.storage.exists(name))

    def test_missing_attachment_blocks_send(self):
        attachment = self.attach()
        self.storage.delete(attachment.file_attachment.name)
        self.message._send()
        self.message.refresh_from_db()
        self.assertFalse(self.message.sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_storage_failure_blocks_send(self):
        self.attach()
        with patch.object(self.storage, 'open', side_effect=PermissionError('Denied')):
            self.message._send()
        self.assertFalse(self.message.sent)
        self.assertEqual(len(mail.outbox), 0)

    def test_replace_uses_storage(self):
        attachment = self.attach()
        old = attachment.file_attachment.name
        attachment.file_attachment.save('new.txt', ContentFile(b'new'), save=False)
        attachment.save()
        self.assertFalse(self.storage.exists(old))
        self.assertTrue(self.storage.exists(attachment.file_attachment.name))

    def test_send_waits_for_commit(self):
        with transaction.atomic():
            self.message.save()
            self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(MAILQUEUE_STORAGE=False)
    def test_disabled_local_storage_uses_default_backend(self):
        from django.core.files.storage import default_storage
        from mailqueue.utils import get_storage

        self.assertIs(get_storage(), default_storage)

    def test_missing_attachment_can_be_retried(self):
        attachment = self.attach()
        name = attachment.file_attachment.name
        self.storage.delete(name)
        self.message._send()
        self.assertEqual(len(mail.outbox), 0)
        self.storage.save(name, ContentFile(b'recovered'))
        self.message._send()
        self.assertTrue(self.message.sent)
        self.assertEqual(len(mail.outbox), 1)

    def test_rollback_does_not_enqueue_send(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                MailerMessage.objects.create(to_address='recipient@example.com', subject='Rolled back')
                raise RuntimeError('rollback')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(MailerMessage.objects.filter(subject='Rolled back').exists())


    def test_raising_mode_preserves_error_and_attempt(self):
        self.attach()
        with patch.object(self.storage, 'open', side_effect=PermissionError('S3 access denied')):
            with self.assertRaisesMessage(PermissionError, 'S3 access denied'):
                self.message._send(raise_errors=True)
        self.message.refresh_from_db()
        self.assertFalse(self.message.sent)
        self.assertIsNotNone(self.message.last_attempt)
        self.assertEqual(len(mail.outbox), 0)

    def test_celery_retry_carries_original_exception(self):
        from celery.exceptions import Retry
        from mailqueue.tasks import send_mail

        self.attach()
        error = PermissionError('S3 access denied')
        with patch.object(self.storage, 'open', side_effect=error), patch.object(
            send_mail, 'retry', side_effect=Retry()
        ) as retry:
            with self.assertRaises(Retry):
                send_mail.run(self.message.pk)
        self.assertIs(retry.call_args.kwargs['exc'], error)
        self.assertEqual(retry.call_args.kwargs['args'], [self.message.pk])
        self.assertEqual(len(mail.outbox), 0)

    def test_retry_exhaustion_reports_original_exception(self):
        from mailqueue.tasks import send_mail

        self.attach()
        with patch.object(self.storage, 'open', side_effect=PermissionError('S3 access denied')):
            with self.assertRaisesMessage(PermissionError, 'S3 access denied'):
                send_mail.apply(args=[self.message.pk], retries=send_mail.max_retries, throw=True)
        self.message.refresh_from_db()
        self.assertFalse(self.message.sent)
        self.assertEqual(len(mail.outbox), 0)
