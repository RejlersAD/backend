"""Names must not invent work or erase restrictions while shortening evidence."""
import unittest
import hashlib

from ..services.activity_naming import proposed_activity_name


class ActivityNamingTests(unittest.TestCase):
    def name(self, statement, excerpt=''):
        return proposed_activity_name(statement, source_excerpt=excerpt)

    def test_work_obligation_has_action_and_specific_subject(self):
        result = self.name('The contractor shall prepare the cable routing layouts.')
        self.assertEqual(result['title'], 'Prepare cable routing layouts')
        self.assertEqual(result['naming_basis'], 'source_obligation')
        self.assertTrue(result['needs_review'])

    def test_bounded_excerpt_completes_wrapped_line(self):
        result = self.name('FEED CONTRACTOR shall prepare the site survey',
                           'Prior information. FEED CONTRACTOR shall prepare the site survey report. Next scope.')
        self.assertEqual(result['title'], 'Prepare site survey report')

    def test_passive_obligation_retains_subject(self):
        self.assertEqual(self.name('Engineering Procedure and QA Plan shall be submitted to COMPANY.')['title'],
                         'Submit engineering procedure and QA plan')

    def test_definition_is_a_requirement_review_not_engineering_work(self):
        self.assertEqual(self.name('The use of the word “shall” indicates a mandatory requirement.')['title'],
                         'Confirm mandatory requirement terminology')

    def test_precedence_and_conflict_notice_have_distinct_names(self):
        self.assertEqual(self.name('In the event of conflict, the order of precedence shall be as follows.')['title'],
                         'Review document order of precedence')
        self.assertEqual(self.name("If such a conflict exists, it shall be the CONTRACTOR's responsibility to bring such conflicts to the COMPANY notice in writing.")['title'],
                         'Notify COMPANY in writing of document conflicts, if any')

    def test_subcontracting_prohibition_cannot_become_execution(self):
        result = self.name('and shall not be sub-contracted to any other company.')
        self.assertIn('prohibition on subcontracting', result['title'])
        self.assertEqual(result['naming_basis'], 'requirement_review')

    def test_generic_negative_keeps_do_not(self):
        self.assertEqual(self.name('The contractor shall not perform demolition work.')['title'],
                         'Review restriction: do not perform demolition work')

    def test_second_prohibition_is_not_truncated_into_positive_work(self):
        result = self.name('The contractor shall confirm components shall not be installed without approval.')
        self.assertEqual(result['title'], 'Review restriction: do not be installed without approval')
        self.assertEqual(result['naming_basis'], 'requirement_review')

    def test_prohibition_exception_remains_visible(self):
        result = self.name('The work shall not be sub-contracted unless COMPANY approves.')
        self.assertIn('unless COMPANY approves', result['title'])

    def test_conditional_demolition_keeps_if_any(self):
        self.assertEqual(self.name('The contractor shall prepare demolition drawings, if any.')['title'],
                         'Prepare demolition drawings (if any)')

    def test_leading_condition_is_not_lost(self):
        result = self.name('If existing UPS systems are inadequate, the contractor shall study new UPS installation.')
        self.assertEqual(result['title'], 'Study new UPS installation (if existing UPS systems are inadequate)')

    def test_parenthesized_condition_remains_visible(self):
        result = self.name('The contractor shall develop a 3D model (if required) for the uncovered area.')
        self.assertIn('(if required)', result['title'])
        self.assertNotIn('3d', result['title'])

    def test_long_name_cannot_truncate_away_condition(self):
        result = self.name('The contractor shall prepare detailed equipment drawings and specifications for all existing electrical distribution panels in all buildings, if required.')
        self.assertLessEqual(len(result['title']), 140)
        self.assertIn('if required', result['title'])

    def test_ift_restriction_preserves_comment_closure(self):
        result = self.name('The contractor shall not proceed for IFT, if all comments are not closed in IFA revision.')
        self.assertEqual(result['title'], 'Confirm no IFT release while IFA comments remain open')

    def test_fragment_asks_for_clarification(self):
        result = self.name('required to fulfil the objectives of the PROJECT.')
        self.assertTrue(result['title'].startswith('Clarify requirement:'))
        self.assertEqual(result['naming_basis'], 'source_fragment_review')

    def test_missing_object_does_not_invent_deliverable(self):
        result = self.name('The contractor shall prepare the')
        self.assertEqual(result['title'], 'Clarify scope of requirement')

    def test_unrelated_excerpt_cannot_override_statement(self):
        self.assertEqual(self.name('The contractor shall prepare MTO.',
                                   'The contractor shall prepare a different package.')['title'], 'Prepare MTO')

    def test_pure_repeatable_and_evidence_independent_identity(self):
        statement = 'The contractor shall review the design basis.'
        a = proposed_activity_name(statement, source_locator={'page': 1})
        b = proposed_activity_name(statement, source_locator={'page': 2})
        self.assertEqual(a, b)
        self.assertNotIn('activity_id', a)

    def test_selected_second_obligation_does_not_reuse_first_name(self):
        result = self.name('and accordingly FEED CONTRACTOR shall prepare the scope of work',
                           'Adequacy shall be checked with OEM and accordingly FEED CONTRACTOR shall prepare the scope of work for modification.')
        self.assertEqual(result['title'], 'Prepare scope of work for modification')

    def test_pdf_bullet_separates_adjacent_requirements_without_full_stop(self):
        result = self.name('(cid:131) FEED CONTRACTOR shall prepare MTO.',
                           'FEED CONTRACTOR shall perform a study (if required) (cid:131) FEED CONTRACTOR shall prepare MTO.')
        self.assertEqual(result['title'], 'Prepare MTO')

    def test_verified_full_source_completes_bound_excerpt_and_preserves_condition(self):
        statement = 'The contractor shall prepare drawings'
        text = 'Prior paragraph. ' + statement + ', if required. Following paragraph.'
        start = text.index(statement)
        locator = {'character_start': start, 'character_end': start + len(statement),
                   'extracted_text_sha256': hashlib.sha256(text.encode()).hexdigest()}
        result = proposed_activity_name(statement, source_excerpt=statement, source_text=text, source_locator=locator)
        self.assertEqual(result['title'], 'Prepare drawings (if required)')

    def test_mismatched_source_hash_does_not_supply_different_condition(self):
        statement = 'The contractor shall prepare drawings'
        result = proposed_activity_name(statement, source_text=statement + ', if required.',
                                        source_locator={'character_start': 0, 'character_end': len(statement),
                                                        'extracted_text_sha256': 'incorrect'})
        self.assertEqual(result['title'], 'Prepare drawings')

    def test_pdf_page_furniture_does_not_become_scope(self):
        result = self.name('The contractor shall review spare IO availability and',
                           'The contractor shall review spare IO availability and AllA plla prtairetsie cso cnosnesnetn tt oto t hthisis.')
        self.assertEqual(result['title'], 'Review spare IO availability')
