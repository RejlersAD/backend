"""Synthetic files only; no real mailbox, webhook credential or database."""
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts import import_sales_test_archive as archive

MAILBOX = 'sales@example.test'


def email_bytes(subject='RADAI-TEST archive', recipient=MAILBOX):
    message = EmailMessage()
    message['From'] = 'Buyer Name <buyer@client.test>'
    message['To'] = recipient
    message['Subject'] = subject
    message['Message-ID'] = '<example@client.test>'
    message['Date'] = 'Mon, 02 Nov 2026 09:00:00 +0000'
    message['Received'] = 'by delivery.example.test; Mon, 02 Nov 2026 13:00:00 +0400'
    message.set_content('Please review this synthetic enquiry.')
    message.add_attachment(b'%PDF-test', maintype='application', subtype='pdf', filename='RFQ.pdf')
    return message.as_bytes()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.incoming = self.root / 'Incoming Emails'
        self.incoming.mkdir()
        self.source = self.incoming / 'Email_123.eml'
        self.source.write_bytes(email_bytes())
        self.checkpoint = self.root / 'checkpoint.json'

    def test_received_time_sender_body_and_attachment_flag(self):
        payload = archive.payload_from_eml(email_bytes(), MAILBOX)
        self.assertEqual(payload['received_at'], '2026-11-02T09:00:00+00:00')
        self.assertEqual(payload['sender_name'], 'Buyer Name')
        self.assertEqual(payload['sender_email'], 'buyer@client.test')
        self.assertTrue(payload['has_attachments'])
        self.assertIn('synthetic enquiry', payload['body_preview'])

    def test_preview_never_calls_intake_or_writes_checkpoint(self):
        with patch.object(archive, 'post_payload', side_effect=AssertionError('HTTP forbidden')):
            result = archive.scan(self.incoming, MAILBOX)
        self.assertEqual(result['ready'], 1)
        self.assertEqual(result['imported'], 0)
        self.assertFalse(self.checkpoint.exists())

    def test_other_subjects_and_mailboxes_are_not_imported(self):
        self.source.write_bytes(email_bytes('Private personal email'))
        self.assertEqual(archive.scan(self.incoming, MAILBOX)['skipped'], 1)
        self.source.write_bytes(email_bytes(recipient='someone@other.test'))
        result = archive.scan(self.incoming, MAILBOX)
        self.assertIn('target_mailbox_not_in_recipients', result['errors'])
        self.assertEqual(result['ready'], 0)

    def test_sent_date_is_not_substituted_for_missing_received_timestamp(self):
        data = email_bytes().replace(b'Received:', b'X-Received:')
        with self.assertRaisesRegex(archive.ArchiveError, 'received_timestamp_missing'):
            archive.payload_from_eml(data, MAILBOX)

    def test_html_body_saved_with_eml_extension_is_not_accepted(self):
        self.source.write_bytes(b'<html><body>RADAI-TEST body only</body></html>')
        result = archive.scan(self.incoming, MAILBOX)
        self.assertEqual(result['ready'], 0)
        self.assertEqual(result['failed'], 1)
        self.assertIn('not_original_eml', result['errors'])

    def test_html_scripts_are_not_sent_and_links_are_not_followed(self):
        message = EmailMessage()
        for key, value in [('From', 'Buyer <buyer@client.test>'), ('To', MAILBOX),
                           ('Subject', 'RADAI-TEST html'),
                           ('Received', 'by test; Mon, 02 Nov 2026 09:00:00 +0000')]:
            message[key] = value
        message.set_content('<p>Visible requirement</p><script>Hidden instruction</script>'
                            '<img src="http://localhost/private">', subtype='html')
        payload = archive.payload_from_eml(message.as_bytes(), MAILBOX)
        self.assertIn('Visible requirement', payload['body_preview'])
        self.assertNotIn('Hidden instruction', payload['body_preview'])

    def test_successful_restart_skips_processed_files_without_network(self):
        original = self.source.read_bytes()
        result = archive.scan(self.incoming, MAILBOX, apply=True, checkpoint=self.checkpoint,
                              send=lambda payload, key: False)
        self.assertEqual(result['imported'], 1)
        result = archive.scan(self.incoming, MAILBOX, apply=True,
                              state=archive.read_state(self.checkpoint), checkpoint=self.checkpoint,
                              send=lambda *args: self.fail('Repeated network request'))
        self.assertEqual(result['unchanged'], 1)
        self.assertEqual(self.source.read_bytes(), original)

    def test_uncertain_delivery_is_retried_with_same_source_identity(self):
        accepted = set()

        def uncertain(payload, key):
            accepted.add(payload['source_message_id'])
            raise archive.ArchiveError('local_backend_unavailable')

        first = archive.scan(self.incoming, MAILBOX, apply=True, checkpoint=self.checkpoint, send=uncertain)
        self.assertEqual(first['failed'], 1)
        self.assertFalse(self.checkpoint.exists())

        def retry(payload, key):
            self.assertIn(payload['source_message_id'], accepted)
            return True

        result = archive.scan(self.incoming, MAILBOX, apply=True, checkpoint=self.checkpoint, send=retry)
        self.assertEqual(result['duplicates'], 1)
        self.assertEqual(len(accepted), 1)

    def test_renamed_copy_keeps_identity_and_changed_bytes_get_new_identity(self):
        identities = []
        data = self.source.read_bytes()
        self.source.rename(self.incoming / 'renamed.eml')
        archive.scan(self.incoming, MAILBOX, apply=True,
                     send=lambda payload, key: identities.append(payload['source_message_id']) or False)
        original = archive.payload_from_eml(data, MAILBOX)['source_message_id']
        self.assertEqual(identities, [original])
        changed = archive.payload_from_eml(data + b'\n', MAILBOX)['source_message_id']
        self.assertNotEqual(original, changed)

    def test_oversized_sources_and_corrupt_checkpoint_are_explicit(self):
        with patch.object(archive, 'MAX_FILE_BYTES', 8):
            self.assertIn('email_too_large', archive.scan(self.incoming, MAILBOX)['errors'])
        self.checkpoint.write_text('{invalid', encoding='utf-8')
        with self.assertRaisesRegex(archive.ArchiveError, 'checkpoint_invalid'):
            archive.read_state(self.checkpoint)

    def test_env_key_is_read_without_logging_or_returning_other_settings(self):
        env = self.root / '.env.local'
        env.write_text('OTHER_SECRET=private\nSALES_EMAIL_INTAKE_WEBHOOK_KEY="' + 'a' * 64 + '"\n')
        self.assertEqual(archive.read_key(env), 'a' * 64)

    def test_os_lock_prevents_two_watchers_and_releases_on_exit(self):
        path = self.root / 'watch.lock'
        with archive.local_lock(path):
            with self.assertRaisesRegex(archive.ArchiveError, 'catchup_already_running'):
                with archive.local_lock(path):
                    self.fail('A second importer acquired the lock')
        with archive.local_lock(path):
            pass

    def test_files_wait_for_review_then_upload_without_reimporting_the_intake(self):
        state = {}
        intakes = []
        uploads = []
        reviewed = False

        def send(payload, key):
            intakes.append(payload['source_message_id'])
            return False

        def handoff(source_id, mailbox, key, data=None):
            if not reviewed:
                self.assertIsNone(data)
                return {'state': 'awaiting_review', 'file_count': 0}
            if data is None:
                return {'state': 'ready', 'file_count': 0}
            uploads.append(data)
            return {'state': 'complete', 'file_count': 2, 'warnings': []}

        first = archive.scan(self.incoming, MAILBOX, apply=True, state=state,
                             archive_files=True, send=send, handoff=handoff)
        self.assertEqual(first['imported'], 1)
        self.assertEqual(first['awaiting_review'], 1)
        self.assertFalse(uploads)
        reviewed = True
        second = archive.scan(self.incoming, MAILBOX, apply=True, state=state,
                              archive_files=True, send=send, handoff=handoff)
        self.assertEqual(second['linked_files'], 2)
        self.assertEqual(len(intakes), 1)
        self.assertEqual(uploads, [self.source.read_bytes()])
        third = archive.scan(self.incoming, MAILBOX, apply=True, state=state,
                             archive_files=True, send=send, handoff=handoff)
        self.assertEqual(third['unchanged'], 1)
        self.assertEqual(len(uploads), 1)

    def test_failed_file_handoff_does_not_lose_intake_acknowledgement(self):
        state = {}

        def failed(*args, **kwargs):
            raise archive.ArchiveError('archive_http_503')

        first = archive.scan(self.incoming, MAILBOX, apply=True, state=state,
                             archive_files=True, send=lambda *args: False, handoff=failed)
        self.assertEqual(first['imported'], 1)
        self.assertEqual(state[self.source.name]['outcome'], 'accepted')
        self.assertNotIn('archive_state', state[self.source.name])
        second = archive.scan(self.incoming, MAILBOX, apply=True, state=state,
                              archive_files=True, send=lambda *args: self.fail('Duplicate intake POST'),
                              handoff=lambda *args, **kwargs: {'state': 'complete', 'file_count': 2})
        self.assertEqual(second['linked_files'], 2)

    def test_legacy_checkpoint_is_upgraded_without_reposting_the_intake(self):
        stat = self.source.stat()
        state = {self.source.name: {'signature': [stat.st_size, stat.st_mtime_ns], 'outcome': 'accepted'}}
        result = archive.scan(self.incoming, MAILBOX, apply=True, state=state, archive_files=True,
                              send=lambda *args: self.fail('Legacy intake was already accepted'),
                              handoff=lambda *args, **kwargs: {'state': 'awaiting_review', 'file_count': 0})
        self.assertEqual(result['awaiting_review'], 1)
        self.assertIn('source_id', state[self.source.name])

    def test_watcher_recovers_from_outage_checkpoints_success_and_stops(self):
        env = self.root / '.env.local'
        secret = 's' * 64
        env.write_text('SALES_EMAIL_INTAKE_WEBHOOK_KEY=' + secret, encoding='utf-8')
        stop_file = self.root / 'watch.stop'
        log_file = self.root / 'watch.log'
        original_scan = archive.scan
        attempts = []
        passes = []

        def send(payload, key):
            attempts.append(payload['source_message_id'])
            if len(attempts) == 1:
                raise archive.ArchiveError('local_backend_unavailable')
            return False

        def sleep(seconds):
            passes.append(seconds)
            if len(passes) == 1:
                self.assertFalse(self.checkpoint.exists())
            else:
                stop_file.touch()

        def run_scan(*args, **kwargs):
            return original_scan(*args, **kwargs, send=send)

        with patch.object(archive, 'scan', side_effect=run_scan):
            archive.watch(self.incoming, MAILBOX, env, self.checkpoint, stop_file, log_file, sleep=sleep)
        self.assertEqual(passes, [60, 60])
        self.assertEqual(attempts[0], attempts[1])
        self.assertTrue(self.checkpoint.exists())
        log = log_file.read_text('utf-8')
        self.assertIn('local_backend_unavailable', log)
        self.assertIn('"imported": 1', log)
        self.assertIn('stopped by request', log)
        self.assertNotIn(secret, log)
        self.assertNotIn('buyer@client.test', log)

    def test_real_loopback_http_round_trip_and_rejected_credentials(self):
        records = set()
        expected_key = 'synthetic-test-key'

        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.headers.get('X-RADAI-Webhook-Key') != expected_key:
                    self.send_response(403)
                    self.end_headers()
                    return
                duplicate = payload['source_message_id'] in records
                records.add(payload['source_message_id'])
                self.send_response(200 if duplicate else 201)
                self.end_headers()
                self.wfile.write(json.dumps({'accepted': True, 'duplicate': duplicate,
                                             'intake_id': str(uuid4())}).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f'http://127.0.0.1:{server.server_port}/api/v1/sales/email-intake/'
            with patch.object(archive, 'ENDPOINT', endpoint):
                payload = archive.payload_from_eml(email_bytes(), MAILBOX)
                self.assertFalse(archive.post_payload(payload, expected_key))
                self.assertTrue(archive.post_payload(payload, expected_key))
                with self.assertRaisesRegex(archive.ArchiveError, 'intake_http_403'):
                    archive.post_payload(payload, 'wrong-key')
            self.assertEqual(len(records), 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_archive_transport_probes_then_sends_exact_original_bytes(self):
        from email import policy
        from email.parser import BytesParser
        captured = []
        original = self.source.read_bytes()
        source_id = archive.payload_from_eml(original, MAILBOX)['source_message_id']

        class Endpoint(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                if self.headers['Content-Type'] == 'application/json':
                    captured.append(json.loads(body))
                    result = {'state': 'ready', 'file_count': 0}
                else:
                    envelope = ('Content-Type: ' + self.headers['Content-Type'] + '\r\n\r\n').encode() + body
                    message = BytesParser(policy=policy.default).parsebytes(envelope)
                    fields = {part.get_param('name', header='content-disposition'): part.get_payload(decode=True)
                              for part in message.iter_parts()}
                    # The email parser interprets message/rfc822 as a nested
                    # message. Check the actual binary multipart field, without
                    # serializing that nested message or normalizing its bytes.
                    fields['file'] = body.split(b'Content-Type: message/rfc822\r\n\r\n', 1)[1].rsplit(
                        b'\r\n--' + message.get_boundary().encode() + b'--\r\n', 1)[0]
                    captured.append(fields)
                    result = {'state': 'complete', 'file_count': 2, 'warnings': []}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())

        server = ThreadingHTTPServer(('127.0.0.1', 0), Endpoint)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.object(archive, 'ARCHIVE_ENDPOINT', f'http://127.0.0.1:{server.server_port}/archive'):
                self.assertEqual(archive.post_archive(source_id, MAILBOX, 'test')['state'], 'ready')
                self.assertEqual(archive.post_archive(source_id, MAILBOX, 'test', data=original)['state'], 'complete')
            self.assertEqual(captured[0], {'source_message_id': source_id})
            self.assertEqual(captured[1]['source_message_id'].decode(), source_id)
            self.assertEqual(captured[1]['mailbox'].decode(), MAILBOX)
            self.assertEqual(captured[1]['file'], original)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
