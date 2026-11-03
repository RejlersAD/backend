"""Synthetic labelled formats, not certification of supplier portal templates."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.sales.email_extraction import _extract_email_fields, extract_email_information
from apps.sales.email_opportunity_evidence import reviewed_analysis_snapshot
from apps.sales.email_analysis import analyze_email_conversation
from apps.sales.tests import test_mailbox_opportunities as fixtures


class CompanyEmailFormatTests(SimpleTestCase):
    def extract(self, body='', subject='RFQ for engineering'):
        return _extract_email_fields(subject=subject, body_text=body)

    def test_company_procurement_labels_preserve_complete_literal_references(self):
        examples = (
            ('ADNOC RFQ No.: 6000012345', '6000012345'),
            ('ADNOC Request for Quotation No.: ADNOC/2026/00123', 'ADNOC/2026/00123'),
            ('Saudi Aramco RFQ: 7000012345', '7000012345'),
            ('Aramco RFQ Number = ARAMCO-RFQ-2026-123', 'ARAMCO-RFQ-2026-123'),
            ('TAQA Tender Reference: TAQA/ITT/2026/123', 'TAQA/ITT/2026/123'),
            ('Borouge RFQ Ref. No.: BOR-RFQ-2026-123', 'BOR-RFQ-2026-123'),
            ('ENOC Enquiry No.: ENOC/ENQ/2026/123', 'ENOC/ENQ/2026/123'),
            ('NMDC Tender No.: NMDC/TND/2026/123', 'NMDC/TND/2026/123'),
            ('ADNOC RFQ 6000012345', '6000012345'),
            ('ADNOC RFQ No.:6000012345', '6000012345'),
            ('Aramco RFQ:7000012345', '7000012345'),
        )
        for line, reference in examples:
            with self.subTest(line=line):
                result = self.extract(line)
                self.assertEqual(result['tender_reference'], reference)
                self.assertIn(reference, result['evidence']['tender_reference'])

    def test_compact_company_rfq_codes_do_not_lose_the_company_prefix(self):
        for company in ('ADNOC', 'ARAMCO', 'TAQA', 'BOROUGE', 'ENOC', 'NMDC'):
            value = f'{company}-RFQ-2026-123'
            with self.subTest(company=company):
                self.assertEqual(self.extract(subject=value)['tender_reference'], value)

    def test_company_reference_without_procurement_type_is_not_a_tender_id(self):
        for company in ('ADNOC', 'Aramco', 'TAQA', 'Borouge', 'ENOC', 'Technip Energies', 'NMDC'):
            line = f'{company} Reference No.: ABC/2026/123'
            with self.subTest(company=company):
                result = self.extract(line, subject='Correspondence')
                self.assertEqual(result['organization_reference'], 'ABC/2026/123')
                self.assertEqual(result['tender_reference'], '')
                self.assertIn(line, result['evidence']['organization_reference'])

    def test_generic_reference_needs_explicit_company_context(self):
        self.assertEqual(self.extract('Reference: ABC-2026-123')['organization_reference'], '')
        result = self.extract('Client Company: TAQA\nReference: ABC-2026-123')
        self.assertEqual(result['organization_reference'], 'ABC-2026-123')
        self.assertEqual(result['customer_name'], 'TAQA')

    def test_technip_document_reference_keeps_its_own_type(self):
        for label in ('Document No.', 'Document Reference', 'Doc. Ref. No.'):
            line = f'Technip Energies {label}: TEN-PRJ-001-MEC-DS-0001'
            with self.subTest(label=label):
                result = self.extract(line, subject='Document review')
                self.assertEqual(result['document_reference'], 'TEN-PRJ-001-MEC-DS-0001')
                self.assertEqual(result['tender_reference'], '')
                self.assertEqual(result['organization_reference'], '')

    def test_conflicting_reference_values_are_not_chosen(self):
        for key, body in (
            ('tender_reference', 'ADNOC RFQ No.: 6000012345\nRFQ No.: 6000054321'),
            ('organization_reference', 'TAQA Ref: TAQA-001\nTAQA Ref: TAQA-002'),
            ('document_reference', 'Technip Energies Document No.: TEN-001\nDocument No.: TEN-002'),
        ):
            with self.subTest(key=key):
                result = self.extract(body)
                self.assertEqual(result[key], '')
                self.assertTrue(any('Conflicting' in warning for warning in result['warnings']))

    def test_invoice_po_and_unlabelled_numbers_are_not_tender_references(self):
        result = self.extract(
            'Client Company: ADNOC\nInvoice No.: 6000012345\nPurchase Order: 7000012345\n'
            'Phone: +971501234567\nDocument No.: RFQ-2026-123',
            subject='Document delivery',
        )
        self.assertEqual(result['tender_reference'], '')
        self.assertEqual(result['document_reference'], 'RFQ-2026-123')

    def test_negated_and_example_references_remain_manual(self):
        for body in ('The RFQ No.: 6000012345 is cancelled.',
                     'Example only: ADNOC RFQ No.: 6000012345',
                     'We have not issued RFQ No.: 6000012345.'):
            with self.subTest(body=body):
                self.assertEqual(self.extract(body)['tender_reference'], '')

    def test_reference_match_does_not_truncate_oversized_tokens(self):
        result = self.extract('ADNOC RFQ No.: ' + 'A' * 125 + '123')
        self.assertEqual(result['tender_reference'], '')

    def test_reference_lookalikes_in_nonprocurement_fields_remain_manual(self):
        for line in ('Document No.: RFQ-2026-123', 'Invoice No.: RFQ-2026-123',
                     'Purchase Order No.: RFQ-2026-123', 'PO: RFQ-2026-123',
                     'No RFQ-2026-123 has been issued.', 'RFQ Reference: see attachment',
                     'RFQ No.: 2026'):
            with self.subTest(line=line):
                self.assertEqual(self.extract(line)['tender_reference'], '')

    def test_typed_references_survive_source_analysis_and_review_snapshot(self):
        body = ('Client Company: Technip Energies\nRFQ No.: TEN/RFQ/2026/123\n'
                'Technip Energies Reference: TEN/REF/2026/123\n'
                'Document No.: TEN-PRJ-001-MEC-DS-0001\nPlease submit your quotation.')
        with patch('apps.sales.email_ai_provider.analyze_email_sources') as provider:
            info = extract_email_information(
                subject='RFQ for engineering', body_text=body,
                sender_email='buyer@customer.test', mailbox_address='sales@consultant.test',
                sent_at='2026-11-03T08:00:00Z',
            )
        for key, value in (('tender_reference', 'TEN/RFQ/2026/123'),
                           ('organization_reference', 'TEN/REF/2026/123'),
                           ('document_reference', 'TEN-PRJ-001-MEC-DS-0001')):
            self.assertEqual(info[key], value)
            self.assertTrue(info['field_sources'][key])
            self.assertIn(value, info['evidence'][key])
            self.assertEqual(reviewed_analysis_snapshot(info)[key], value)
        provider.assert_not_called()

    def test_due_closing_and_bid_submission_labels(self):
        for label in ('Due Date', 'Tender Closing Date', 'Bid Submission Date',
                      'Bid Submission Deadline', 'RFQ Closing Date', 'Tender Closing Date & Time',
                      'Last date for bid submission', 'Quotation submission date'):
            with self.subTest(label=label):
                result = self.extract(f'{label}: 20 November 2026')
                self.assertEqual(result['due_date'], '2026-11-20')
                self.assertEqual(result['submission_date'], '')
                self.assertIn('20 November 2026', result['evidence']['due_date'])

    def test_supported_date_spellings_remain_unambiguous(self):
        for text in ('2026/11/20', '20.11.2026', '20 NOV 2026', '20NOV2026', 'Nov. 20, 2026'):
            with self.subTest(text=text):
                self.assertEqual(self.extract(f'Bid Submission Date: {text}')['due_date'], '2026-11-20')
        for text in ('03.04.2026', '03/04/2026', '31/11/2026', '2026/02/30'):
            with self.subTest(text=text):
                result = self.extract(f'Tender Closing Date: {text}')
                self.assertEqual(result['due_date'], '')
                self.assertTrue(result['warnings'])

    def test_unrelated_or_competing_dates_are_not_submission_deadlines(self):
        for text in ('Invoice due date: 20 November 2026', 'Payment due date: 20 November 2026',
                     'RFQ issue date: 20 November 2026', 'Bid opening date: 20 November 2026',
                     'Tender fee due date: 20 November 2026',
                     'Cancelled bid submission date: 20 November 2026',
                     'Agreement return due date: 20 November 2026'):
            with self.subTest(text=text):
                self.assertEqual(self.extract(text)['due_date'], '')
        result = self.extract('Due date: 20 November 2026\nBid submission date: 21 November 2026')
        self.assertEqual(result['due_date'], '')
        self.assertTrue(result['warnings'])

    def test_currency_values_require_explicit_commercial_context(self):
        for text, value, currency in (
            ('Estimated project value: AED 1,250,000.50', '1250000.50', 'AED'),
            ('Budget amount = USD750,000', '750000', 'USD'),
            ('Contract value: 250000.00 USD', '250000.00', 'USD'),
            ('The estimated contract value is US$ 500,000.', '500000', 'USD'),
            ('Project value: Dhs. 125,000 (excluding VAT)', '125000', 'AED'),
        ):
            with self.subTest(text=text):
                result = self.extract(text)
                self.assertEqual((result['estimated_value'], result['currency']), (value, currency))
                self.assertIn(text, result['evidence']['estimated_value'])

    def test_fees_ranges_percentages_scaled_or_malformed_values_are_not_guessed(self):
        for text in ('Tender fee: AED 500', 'Bid bond: USD 50,000',
                     'Invoice total: AED 20,000', 'Project value: AED 10,00',
                     'Project value: USD 1.234,56', 'Project value: AED 2 million',
                     'Project value: USD 500-1000', 'Project value: USD 500 to 1000',
                     'Project value: AED 20%', 'Project value: $500',
                     'Project value: AED -500', 'Project value: AED 1,000 per month'):
            with self.subTest(text=text):
                self.assertEqual(self.extract(text)['estimated_value'], '')

    def test_equivalent_money_values_agree_but_currency_conflicts_require_review(self):
        result = self.extract('Budget: AED 1,000\nEstimated value: AED 1000.00')
        self.assertEqual(result['estimated_value'], '1000')
        self.assertFalse(result['warnings'])
        for body in ('Budget: AED 1000\nBudget: USD 1000', 'Budget: USD 1000\nCurrency: AED'):
            with self.subTest(body=body):
                result = self.extract(body)
                self.assertEqual(result['estimated_value'], '')
                self.assertEqual(result['currency'], '')
                self.assertTrue(result['warnings'])

    def test_project_labels_and_subject_markers_without_guessing_from_company(self):
        for label in ('Project Name', 'Project Title', 'Name of Project'):
            with self.subTest(label=label):
                result = self.extract(f'{label} = Synthetic Offshore Expansion')
                self.assertEqual(result['project_name'], 'Synthetic Offshore Expansion')
        result = self.extract(subject='ADNOC RFQ-2026-123 | Project: Synthetic Gas Upgrade | Revision 2')
        self.assertEqual(result['project_name'], 'Synthetic Gas Upgrade')
        self.assertEqual(self.extract('Client Company: ADNOC')['project_name'], '')
        self.assertEqual(self.extract('Project Name: First\nProject Title: Second')['project_name'], '')

    def test_equal_amounts_with_different_currencies_in_separate_messages_do_not_combine(self):
        messages = [{
            'id': f'item-{index}', 'subject': 'RFQ for engineering',
            'body_text': f'Please submit a quotation.\nProject value: {currency} {amount}',
            'sender_email': 'buyer@customer.test', 'direction': 'incoming',
            'sent_at': f'2026-11-0{index + 1}T08:00:00Z',
        } for index, (currency, amount) in enumerate((('AED', '1000'), ('USD', '1000.00')))]
        info = analyze_email_conversation(
            messages, selected_message_id='item-1', mailbox_address='sales@consultant.test',
        )['extracted_information']
        self.assertEqual(info['estimated_value'], '')
        self.assertEqual(info['currency'], '')
        self.assertTrue(info['warnings'])


@override_settings(ROOT_URLCONF='apps.sales.tests.test_mailbox_opportunities',
                   SALES_EMAIL_AI_ENABLED=True,
                   SALES_MICROSOFT_GRAPH_BASE_URL='https://graph.microsoft.com/v1.0')
class CompanyFormatAPITests(TestCase):
    setUp = fixtures.MailboxOpportunityAPITests.setUp
    review = fixtures.MailboxOpportunityAPITests.review
    post = fixtures.MailboxOpportunityAPITests.post

    def roundtrip(self, saved):
        from apps.sales.models import Deal, OpportunityAuditEvent, SalesEmailIntake
        from apps.sales.tests.test_mailbox_browsing import graph_response
        self.account.company_name = 'Technip Energies'
        self.account.save(update_fields=['company_name'])
        body = ('Client Company: Technip Energies\nRFQ No.: TEN/RFQ/2026/123\n'
                'Technip Energies Reference: TEN/REF/2026/123\n'
                'Document No.: TEN-PRJ-001-MEC-DS-0001\n'
                'Bid Submission Date: 20 November 2026\nEstimated project value: USD 125,000.50\n'
                'Project Title: Synthetic Gas Upgrade\nPlease submit your quotation.')
        if saved:
            intake = SalesEmailIntake.objects.create(
                source_message_id='synthetic-company-format', subject='RFQ for engineering',
                sender_email='buyer@customer.test', body_preview=body, received_at='2026-11-03T08:00:00Z',
            )
            url = f'/api/v1/sales/email-intakes/{intake.pk}/'
        else:
            self.message.update({'subject': 'RFQ for engineering', 'body': {'contentType': 'text', 'content': body}})
            self.network.return_value = graph_response(self.message)
        with patch('apps.sales.email_ai_analysis.analyze_email_sources') as provider:
            response = self.client.get(url) if saved else self.review()
            self.assertEqual(response.status_code, 200, response.data)
            detail = response.data
            info = detail['extracted_information']
            self.assertEqual(info['document_reference'], 'TEN-PRJ-001-MEC-DS-0001')
            self.assertEqual(info['organization_reference'], 'TEN/REF/2026/123')
            self.assertEqual(info['tender_reference'], 'TEN/RFQ/2026/123')
            self.assertEqual(info['project_name'], 'Synthetic Gas Upgrade')
            self.assertEqual(info['estimated_value'], '125000.50')
            self.assertEqual(info['currency'], 'USD')
            self.assertEqual(info['due_date'], '2026-11-20')
            self.assertNotIn('ai_review', info)
            payload = {**self.fields, 'source_token': detail['source_token'],
                       'client_reference': info['tender_reference'], 'submission_due_date': info['due_date'],
                       'estimated_value': info['estimated_value'], 'currency': info['currency']}
            for _ in range(2):
                result = self.client.post(url + 'convert-to-opportunity/', payload, format='json') if saved else self.post(payload)
                self.assertIn(result.status_code, (200, 201), result.data)
            provider.assert_not_called()
        self.assertEqual(Deal.objects.count(), 1)
        snapshot = OpportunityAuditEvent.objects.get().data['reviewed_email_analysis']
        self.assertEqual(snapshot['document_reference'], info['document_reference'])
        self.assertEqual(snapshot['organization_reference'], info['organization_reference'])
        self.assertEqual(Deal.objects.get().client_reference, info['tender_reference'])

    def test_live_rules_and_reviewed_creation_keep_typed_source_references(self):
        self.roundtrip(saved=False)

    def test_saved_rules_and_reviewed_creation_keep_typed_source_references(self):
        self.roundtrip(saved=True)
