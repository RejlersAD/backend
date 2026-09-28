from django.test import SimpleTestCase

from apps.sales.email_extraction import _extract_email_fields


class EmailExtractionTests(SimpleTestCase):
    def extract(self, body="", subject="New enquiry", sender="contact@example.test"):
        # Flat field rules retain organization/date labels; public v2 semantics
        # (customer domain and original sent date) have separate API tests.
        return _extract_email_fields(subject=subject, body_text=body, sender_email=sender)

    def test_explicit_requested_fields_and_reviewable_form_defaults(self):
        data = self.extract(
            "Customer Name: Example Energy LLC\n"
            "Submission Date: 28 September 2026\n"
            "Due Date: 15 October 2026\n"
            "Expected award date: 30 October 2026\n"
            "Estimated value: AED 850,000.50\n"
            "Scope summary: Detailed engineering for a pump station\n",
            subject="RFT-2026-001 | Pump station",
        )
        self.assertEqual(data["title"], "RFT-2026-001 | Pump station")
        self.assertEqual(data["customer_name"], "Example Energy LLC")
        self.assertEqual(data["company_name"], data["customer_name"])
        self.assertEqual(data["submission_date"], "2026-09-28")
        self.assertEqual(data["due_date"], "2026-10-15")
        self.assertEqual(data["deadline_date"], "2026-10-15")
        self.assertEqual(data["expected_award_date"], "2026-10-30")
        self.assertEqual(data["request_type_code"], "RFT")
        self.assertEqual(data["tender_reference"], "RFT-2026-001")
        self.assertEqual(data["estimated_value"], "850000.50")
        self.assertEqual(data["currency"], "AED")
        self.assertEqual(data["scope_type"], "detailed_engineering")
        self.assertIn("15 October 2026", data["evidence"]["due_date"])
        self.assertEqual(data["warnings"], [])

    def test_missing_evidence_does_not_invent_company_dates_money_or_currency(self):
        data = self.extract("Hello, please contact us.", sender="purchasing@verylargecompany.test")
        for field in ("customer_name", "submission_date", "due_date", "expected_award_date", "estimated_value", "currency", "scope_type", "request_type_code"):
            self.assertEqual(data[field], "", field)
        self.assertEqual(data["client_domain"], "verylargecompany.test")

    def test_request_codes_have_boundaries_and_do_not_treat_rfq_as_rft(self):
        for source, expected in (("Expression of interest", "EOI"), ("Request for Tender", "RFT"), ("EIO-2026-123", "EIO")):
            with self.subTest(source=source):
                self.assertEqual(self.extract(subject=source)["request_type_code"], expected)
        data = self.extract("The submitted report was written yesterday.")
        self.assertEqual(data["request_type"], "General client email")
        data = self.extract(subject="RFQ for work")
        self.assertEqual(data["request_type"], "Request for quotation")
        self.assertEqual(data["request_type_code"], "RFQ")

    def test_conflicting_request_types_remain_for_review(self):
        data = self.extract("Earlier EOI has now progressed to RFT.")
        self.assertEqual(data["request_type_code"], "")
        self.assertEqual(data["request_type"], "")
        self.assertEqual(data["request_match_strength"], "conflicting")
        self.assertTrue(data["warnings"])

    def test_ambiguous_numeric_date_keeps_raw_evidence(self):
        data = self.extract("Due date: 03/04/2026")
        self.assertEqual(data["due_date"], "")
        self.assertEqual(data["due_date_text"], "03/04/2026")
        self.assertTrue(data["warnings"])

    def test_unambiguous_numeric_dates_are_not_locale_guesses(self):
        for raw in ("23/10/2026", "10/23/2026", "2026-10-23", "23rd October 2026", "October 23, 2026", "23-Oct-2026"):
            with self.subTest(raw=raw):
                self.assertEqual(self.extract(f"Due date: {raw}")["due_date"], "2026-10-23")

    def test_invalid_and_conflicting_dates_remain_blank(self):
        for body in ("Due date: 31 February 2026", "Due date: 2026-10-23\nDeadline: 2026-10-24", "Due date: 2026-10-23\nDeadline: 03/04/2026"):
            with self.subTest(body=body):
                data = self.extract(body)
                self.assertEqual(data["due_date"], "")
                self.assertTrue(data["warnings"])

    def test_submission_date_does_not_set_due_date_or_award_date(self):
        data = self.extract("Submission date: 2026-10-23")
        self.assertEqual(data["submission_date"], "2026-10-23")
        self.assertEqual(data["deadline_date"], "")
        self.assertEqual(data["expected_award_date"], "")
        data = self.extract("Required submission date: 2026-10-23")
        self.assertEqual(data["due_date"], "2026-10-23")
        self.assertEqual(data["submission_date"], "")

    def test_conflicting_customer_names_are_not_chosen_by_first_match(self):
        data = self.extract("Customer: Example Energy LLC\nClient name: Other Energy Ltd")
        self.assertEqual(data["customer_name"], "")
        self.assertIn("Example Energy", data["evidence"]["customer_name"])
        self.assertTrue(data["warnings"])

    def test_structured_table_text_and_repeat_evidence(self):
        data = self.extract("Customer name\tExample Energy LLC\nDue date:\t2026-10-23\nDue date: 23 October 2026")
        self.assertEqual(data["customer_name"], "Example Energy LLC")
        self.assertEqual(data["due_date"], "2026-10-23")
        self.assertFalse(data["warnings"])

    def test_value_does_not_truncate_localized_or_unqualified_numbers(self):
        for value in ("AED 1.234,56", "AED -500", "AED 2 million", "AED 10,00", "AED 1.234"):
            with self.subTest(value=value):
                self.assertEqual(self.extract(f"Budget: {value}")["estimated_value"], "")
        data = self.extract("Budget: 250000.00 USD")
        self.assertEqual(data["estimated_value"], "250000.00")
        self.assertEqual(data["currency"], "USD")

    def test_scope_keywords_do_not_match_feedback_and_keep_conflicts(self):
        self.assertEqual(self.extract("Please send feedback on this study.")["scope_type"], "")
        self.assertEqual(self.extract("Pre-FEED services requested.")["scope_type"], "pre_feed")
        data = self.extract("FEED followed by detailed engineering.")
        self.assertEqual(data["scope_type"], "")
        self.assertTrue(data["warnings"])

    def test_long_input_has_bounded_review_notice_without_external_processing(self):
        data = self.extract("X" * 250_001 + "\nCustomer: Out of bounds")
        self.assertEqual(data["customer_name"], "")
        self.assertTrue(data["warnings"])

    def test_leading_folded_sender_header_provides_explicit_contact_and_organization(self):
        body = ('From: Priya Nair\n(Aster Marine Services)\n'
                '<priya@aster.example>\n\nPlease review the requirements.')
        data = self.extract(body)
        self.assertEqual(data['customer_name'], 'Aster Marine Services')
        self.assertEqual(data['contact_name'], 'Priya Nair')
        self.assertEqual(data['contact_email'], 'priya@aster.example')
        for key in ('customer_name', 'contact_name', 'contact_email'):
            self.assertIn(data['evidence'][key], body)

    def test_later_sender_header_is_not_attributed_to_current_sender(self):
        data = self.extract('Please review the earlier email.\n\nFrom: Jules Chen\n'
                            '(Old Organization)\n<jules@old.example>')
        for key in ('customer_name', 'contact_name', 'contact_email'):
            self.assertEqual(data[key], '')

    def test_header_and_explicit_organization_conflict_remains_unresolved(self):
        data = self.extract('From: Lea (Aster Marine) <lea@aster.example>\n\nCustomer: Basin Energy')
        self.assertEqual(data['customer_name'], '')
        self.assertIn('Aster Marine', data['evidence']['customer_name'])
        self.assertIn('Basin Energy', data['evidence']['customer_name'])

    def test_sender_metadata_parenthesized_company_requires_organization_evidence(self):
        data = _extract_email_fields(sender_name='Tara Bose (Westlake Utilities)', sender_email='tara@westlake.example')
        self.assertEqual(data['customer_name'], 'Westlake Utilities')
        self.assertEqual(data['contact_name'], 'Tara Bose')
        self.assertEqual(data['evidence']['customer_name'], 'From: Tara Bose (Westlake Utilities) <tara@westlake.example>')
        for role in ('External', 'Sales', 'Project team', 'Manager'):
            with self.subTest(role=role):
                data = _extract_email_fields(sender_name=f'Tara Bose ({role})', sender_email='tara@westlake.example')
                self.assertEqual(data['customer_name'], '')
                self.assertEqual(self.extract(f'From: Tara Bose ({role}) <tara@westlake.example>')['customer_name'], '')

    def test_organization_conducting_wrapped_market_exercise_is_evidenced(self):
        body = ('North Coast Utilities is currently conducting a market\n'
                'benchmarking exercise and invites you to submit a budgetary quotation.')
        data = self.extract(body)
        self.assertEqual(data['customer_name'], 'North Coast Utilities')
        self.assertIn(data['evidence']['customer_name'], body)
        self.assertEqual(data['request_type_code'], 'RFQ')
        self.assertEqual(data['request_match_strength'], 'explicit')

    def test_generic_organization_pronoun_does_not_create_company(self):
        data = self.extract('We are currently conducting a procurement exercise.')
        self.assertEqual(data['customer_name'], '')

    def test_project_name_uses_explicit_label_or_bounded_request_subject(self):
        subject = 'Request for Budgetary Quotation -\nHarbor Cooling Project'
        data = self.extract(subject=subject)
        self.assertEqual(data['project_name'], 'Harbor Cooling Project')
        self.assertIn(data['evidence']['project_name'], subject)
        data = self.extract('Project name: Eastern Pipeline', subject=subject)
        self.assertEqual(data['project_name'], 'Eastern Pipeline')
        self.assertEqual(self.extract(subject='General project correspondence')['project_name'], '')
        self.assertEqual(self.extract('Project: Alpha\nProject name: Beta')['project_name'], '')

    def test_explicit_request_precedes_weak_generic_words_with_source_strength(self):
        data = self.extract('Please include a quotation and technical proposal.', subject='RFT-203: Pump design')
        self.assertEqual(data['request_type_code'], 'RFT')
        self.assertEqual(data['request_match_strength'], 'explicit')
        self.assertEqual(data['evidence']['request_type_code'], 'RFT')

    def test_weak_request_keywords_are_distinct_from_explicit_or_conflicting_evidence(self):
        data = self.extract('We need the quotation for this work.')
        self.assertEqual(data['request_type_code'], 'RFQ')
        self.assertEqual(data['request_match_strength'], 'keyword')
        data = self.extract('The quotation and proposal are both discussed.')
        self.assertEqual(data['request_type_code'], '')
        self.assertEqual(data['request_match_strength'], 'conflicting')
        self.assertTrue(data['warnings'])
        self.assertEqual(self.extract('Thank you for the update.')['request_match_strength'], 'none')

    def test_folded_formal_request_has_literal_evidence(self):
        body = 'We issue a request for\nbudgetary quotation for the survey.'
        data = self.extract(body)
        self.assertEqual(data['request_type_code'], 'RFQ')
        self.assertEqual(data['request_match_strength'], 'explicit')
        self.assertIn(data['evidence']['request_type_code'], body)

    def test_negated_request_does_not_return_as_legacy_description(self):
        data = self.extract('We are not issuing a request for\nquotation.')
        self.assertEqual(data['request_type_code'], '')
        self.assertEqual(data['request_type'], 'General client email')
        self.assertEqual(data['request_match_strength'], 'none')

    def test_multiline_natural_deadline_keeps_literal_evidence(self):
        body = ('The deadline for submission of the budgetary quotation\n'
                'is 17 November 2026. Please contact the buyer for questions.')
        data = self.extract(body)
        self.assertEqual(data['due_date'], '2026-11-17')
        self.assertEqual(data['submission_date'], '')
        self.assertIn(data['evidence']['due_date'], body)

    def test_deadline_requires_bounded_clause_not_blank_paragraph_or_unrelated_date(self):
        for body in ('The deadline for submission is\n\n17 November 2026.',
                     'Deadline for submission\nsee the attached documents\n17 November 2026.',
                     'The meeting is on 17 November 2026.',
                     'The deadline for submission of the quotation is in the attachment.'):
            with self.subTest(body=body):
                self.assertEqual(self.extract(body)['due_date'], '')

    def test_negated_hypothetical_and_proposed_deadlines_are_not_current_dates(self):
        for body in ('The deadline is not 17 November 2026.',
                     'If approved, the deadline for submission\nis 17 November 2026.',
                     'Proposed deadline: 17 November 2026.',
                     'Due date: 17 November 2026 is not confirmed.',
                     'Could the deadline be 17 November 2026?'):
            with self.subTest(body=body):
                self.assertEqual(self.extract(body)['due_date'], '')

    def test_no_later_than_and_month_may_are_not_negative_qualifiers(self):
        data = self.extract('Please submit the quotation no later than May 2, 2027.')
        self.assertEqual(data['due_date'], '2027-05-02')
        self.assertEqual(data['request_type_code'], 'RFQ')
        data = self.extract('Tender No 187. Due date: 17 November 2026.')
        self.assertEqual(data['due_date'], '2026-11-17')

    def test_fee_due_date_and_unqualified_money_do_not_become_bid_deadline_or_value(self):
        data = self.extract('The registration fee due date is 17 November 2026.\n'
                            'Please pay AED 750 to download the tender documents.')
        self.assertEqual(data['due_date'], '')
        self.assertEqual(data['estimated_value'], '')
        self.assertEqual(data['currency'], '')

    def test_natural_deadline_numeric_ambiguity_still_requires_review(self):
        data = self.extract('The deadline for submission of the quotation\nis 03/04/2027.')
        self.assertEqual(data['due_date'], '')
        self.assertEqual(data['due_date_text'], '03/04/2027')
        self.assertTrue(data['warnings'])

    def test_explicit_customer_domain_and_website_are_evidence_not_validated_identity(self):
        for label, value in (('Customer domain', 'north.example'),
                             ('Client website', 'https://north.example/sourcing'),
                             ('Customer website', 'https://north.example')):
            body = f'{label}: {value}'
            with self.subTest(label=label):
                data = self.extract(body)
                self.assertEqual(data['declared_client_domain'], value)
                self.assertEqual(data['evidence']['declared_client_domain'], body)
                self.assertEqual(data['customer_name'], '')
