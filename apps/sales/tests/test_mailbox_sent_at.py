"""Genuine source timestamps remain distinct from reception and inference."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.sales.mailbox_capture import validate_captured_message
from apps.sales.microsoft_graph import SalesMailboxReadError
from apps.sales.saved_email_analysis import _message


class CapturedSentTimeTests(SimpleTestCase):
    def source(self, **extra):
        return {
            'subject': 'Synthetic request', 'sender_name': 'Synthetic buyer',
            'sender_email': 'buyer@customer.test', 'received_at': '2026-09-28T08:00:00Z',
            'body_text': 'Synthetic source.', 'has_attachments': False, 'importance': 'normal',
            'conversation_id': 'synthetic-conversation', 'internet_message_id': '', **extra,
        }

    def test_aware_timestamp_preserves_instant_even_across_calendar_days(self):
        source = validate_captured_message(self.source(sent_at='2026-09-27T23:58:00-04:00'))
        self.assertEqual(source['sent_at'], datetime(2026, 9, 28, 3, 58, tzinfo=timezone.utc))
        self.assertNotEqual(source['sent_at'], source['received_at'])

    def test_missing_and_explicit_null_are_unknown_not_received_time(self):
        for extra in ({}, {'sent_at': None}):
            self.assertIsNone(validate_captured_message(self.source(**extra))['sent_at'])

    def test_explicit_invalid_timestamps_never_gain_an_assumed_timezone_or_leak_content(self):
        for value in ('', '2026-09-28', '2026-09-28T08:00:00', 'private-invalid-sent-time',
                      '2026-02-30T08:00:00Z', 1727500000, True, [], {}):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(SalesMailboxReadError) as failure:
                    validate_captured_message(self.source(sent_at=value))
                self.assertEqual(failure.exception.code, 'unsupported_source')
                self.assertNotIn('private-invalid-sent-time', str(failure.exception))

    def test_saved_projection_passes_actual_sent_time_for_selected_and_sibling_sources(self):
        received = datetime(2026, 9, 28, 8, tzinfo=timezone.utc)
        sent = received - timedelta(hours=3)
        source = {
            'id': 'synthetic-saved-id', 'source_message_id': 'synthetic-message',
            'internet_message_id': '', 'subject': 'Synthetic request', 'sender_name': 'Buyer',
            'sender_email': 'buyer@customer.test', 'received_at': received, 'sent_at': sent,
            'body_preview': 'Synthetic evidence.', 'has_attachments': False,
        }
        for value, selected in ((SimpleNamespace(**source), True), (source, False)):
            projected = _message(value, selected=selected, mailbox_address='sales@example.test')
            self.assertEqual(projected['sent_at'], sent.isoformat())
            self.assertEqual(projected['received_at'], received.isoformat())
        source['sent_at'] = None
        projected = _message(source, mailbox_address='sales@example.test')
        self.assertEqual(projected['sent_at'], '')
        self.assertEqual(projected['received_at'], received.isoformat())
