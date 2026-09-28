from django.test import SimpleTestCase

from apps.sales.email_extraction import extract_email_information


class EmailExtractionTests(SimpleTestCase):
    def extract(self, body="", subject="New enquiry", sender="contact@example.test"):
        return extract_email_information(subject=subject, body_text=body, sender_email=sender)

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
        self.assertEqual(data["request_type_code"], "")

    def test_conflicting_request_types_remain_for_review(self):
        data = self.extract("Earlier EOI has now progressed to RFT.")
        self.assertEqual(data["request_type_code"], "")
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
