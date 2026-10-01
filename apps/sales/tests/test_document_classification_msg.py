"""Real compound-file fixtures; no Outlook application or provider calls."""
import base64
from io import BytesIO
import json
from pathlib import Path
import struct
import unittest

from apps.sales.document_classification_msg import MAX_SOURCE, MAX_TEXT, _PassiveText, extract_msg_text


FIXTURES = json.loads(Path(__file__).with_name('fixtures').joinpath('classification-messages.json').read_text('utf-8'))


class MessageExtractionTests(unittest.TestCase):
    def source(self, name='plain'):
        return base64.b64decode(FIXTURES[name])

    def test_real_unicode_message_has_headers_body_without_attachment_contents(self):
        value = self.source()
        stream = BytesIO(value)
        stream.seek(7)
        text, error = extract_msg_text(stream, len(value))
        self.assertEqual(error, '')
        for expected in ('Subject: Synthetic bid clarification', 'Sender: sender@example.invalid',
                         'Technical proposal', 'العرض الفني', '€ 123'):
            self.assertIn(expected, text)
        self.assertNotIn('Synthetic attachment content', text)
        self.assertEqual(stream.tell(), 7)
        self.assertFalse(stream.closed)

    def test_rich_message_extracts_passive_text_without_script_or_remote_urls(self):
        value = self.source('html')
        text, error = extract_msg_text(BytesIO(value), len(value))
        self.assertEqual(error, '')
        self.assertIn('Client clarification', text)
        self.assertIn('Review the attached scope', text)
        self.assertNotIn('window.officePreviewExecuted', text)
        self.assertNotIn('https://', text)
        self.assertNotIn('Synthetic attachment content', text)

    def test_missing_body_retains_headers_with_explicit_partial_diagnostic(self):
        value = self.source('empty')
        text, error = extract_msg_text(BytesIO(value), len(value))
        self.assertEqual(error, 'msg_body_unavailable')
        self.assertIn('Subject:', text)

    def test_bad_header_truncation_and_impossible_fat_are_rejected(self):
        value = self.source()
        invalid_fat = bytearray(value)
        struct.pack_into('<I', invalid_fat, 44, 0x7fffffff)
        for payload in (b'not a message', value[:200], value[:-1], bytes(invalid_fat)):
            with self.subTest(size=len(payload)):
                self.assertEqual(extract_msg_text(BytesIO(payload), len(payload)), ('', 'invalid_document'))

    def test_oversized_message_returns_before_read(self):
        class Unreadable:
            def tell(self):
                return 0

            def seek(self, position):
                self.position = position

            def read(self, *args):
                raise AssertionError('Oversized message must not be parsed')

        self.assertEqual(extract_msg_text(Unreadable(), MAX_SOURCE + 1), ('', 'source_too_large'))

    def test_html_omits_nested_active_content_and_bounds_text(self):
        parser = _PassiveText()
        parser.feed('<h1>Visible</h1><svg><text>Hidden</text></svg><script>secret()</script><p>' + 'x' * (MAX_TEXT * 2) + '</p>')
        result = ''.join(parser.parts)
        self.assertIn('Visible', result)
        self.assertNotIn('Hidden', result)
        self.assertNotIn('secret', result)
        self.assertLessEqual(len(result), MAX_TEXT)


if __name__ == '__main__':
    unittest.main()
