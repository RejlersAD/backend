from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from apps.hr_core.assistant import answer_question, redact_external_prompt
from apps.hr_core.governance import _redact
from apps.hr_core.microsoft_graph import MicrosoftGraphService, extract_document_text


class MicrosoftGraphServiceTests(SimpleTestCase):
    @override_settings(MICROSOFT_GRAPH_TIMEOUT=7)
    @patch.dict('os.environ', {'MICROSOFT_GRAPH_CLIENT_SECRET': 'private-value'})
    @patch('apps.hr_core.microsoft_graph.requests.post')
    def test_client_credentials_uses_default_scope_and_does_not_store_secret(self, post):
        response = Mock()
        response.json.return_value = {'access_token': 'token'}
        response.raise_for_status.return_value = None
        post.return_value = response
        connection = SimpleNamespace(tenant_id='tenant', client_id='client')
        service = MicrosoftGraphService(connection)
        self.assertEqual(service.token(), 'token')
        self.assertEqual(post.call_args.kwargs['data']['scope'], 'https://graph.microsoft.com/.default')
        self.assertFalse(hasattr(connection, 'client_secret'))

    def test_text_policy_extraction(self):
        self.assertEqual(extract_document_text('leave.md', b'Annual leave policy'), 'Annual leave policy')


class HRAssistantSafetyTests(SimpleTestCase):
    @patch('apps.hr_core.assistant.retrieve_policy_passages', return_value=[])
    def test_no_authorized_evidence_fails_closed(self, _retrieve):
        result = answer_question(SimpleNamespace(), 'What is my leave entitlement?')
        self.assertFalse(result['grounded'])
        self.assertEqual(result['refusal_reason'], 'no_authorized_policy_evidence')

    def test_audit_metadata_redacts_credentials_and_payroll_identifiers(self):
        safe = _redact({'token': 'abc', 'nested': {'iban': 'AE123'}, 'count': 2})
        self.assertEqual(safe['token'], '[REDACTED]')
        self.assertEqual(safe['nested']['iban'], '[REDACTED]')
        self.assertEqual(safe['count'], 2)

    def test_external_prompt_redacts_contact_identifiers(self):
        prompt = redact_external_prompt('Check jane@example.com or +971 50 123 4567')
        self.assertNotIn('jane@example.com', prompt)
        self.assertNotIn('123 4567', prompt)
