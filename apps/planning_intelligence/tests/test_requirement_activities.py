"""Deterministic activity selection keeps the actual uploaded scope."""
import hashlib
import unittest

from ..services.requirement_activities import extract_requirement_activities


class RequirementActivityTests(unittest.TestCase):
    def source(self, text, file_id=7):
        return {'id': file_id, 'original_filename': 'scope.pdf', 'category': 'sow', 'extracted_text': text}

    def fact(self, text, quote, fact_id=1, file_id=7):
        start = text.index(quote)
        return {'id': fact_id, 'value': quote, 'source_file_id': file_id, 'source_locator': {
            'character_start': start, 'character_end': start + len(quote),
            'extracted_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
        }}

    def test_explicit_list_preserves_wrapped_pdf_rows_and_source_groups(self):
        text = ('APPENDIX 3 – FEED DELIVERABLES\fTable 7: FEED Deliverables\n'
                'S. No. Description\nGeneral\n1 Project Execution and Quality Plan\n'
                '2 Site Survey Report (OEM)\nElectrical\n1 Electrical Load List\n'
                '2 Electrical Interface drawing between electrical system\nand Instrumentation.\n'
                'Civil & Structural\n1 Adequacy Check Calculations\n'
                'Adequacy Check Reports and Modification Drawings of existing buildings /\n2\nstructures\n'
                '3 Road Crossing Details\nPlease consider below mentioned\n'
                '1. This deliverable list is only a tentative list.\nAPPENDIX 4 – LIST OF SOFTWARES\n'
                '1 Design Software\n')
        result = extract_requirement_activities([self.source(text)])
        tasks = result['tasks']
        self.assertEqual(len(tasks), 7)
        self.assertEqual(tasks[3]['title'], 'Electrical Interface drawing between electrical system and Instrumentation.')
        self.assertEqual(tasks[3]['source_item_number'], '2')
        self.assertEqual(tasks[5]['title'], 'Adequacy Check Reports and Modification Drawings of existing buildings / structures')
        self.assertEqual(tasks[5]['discipline'], 'civil')
        self.assertEqual(tasks[5]['source_item_number'], '2')
        for task in tasks:
            loc = task['source_locator']
            self.assertEqual(task['source_quote'], text[loc['character_start']:loc['character_end']])
            self.assertEqual(loc['extracted_text_sha256'], hashlib.sha256(text.encode()).hexdigest())
            self.assertTrue(task['needs_review'])
            self.assertNotIn('duration', task)
            self.assertNotIn('start_date', task)
            self.assertNotIn('dependencies', task)

    def test_authoritative_list_does_not_duplicate_narrative_requirements(self):
        statement = 'The contractor shall prepare the site survey report.'
        text = statement + '\nDeliverables\n1 Site Survey Report\n'
        result = extract_requirement_activities([self.source(text)], [self.fact(text, statement)])
        self.assertEqual([task['title'] for task in result['tasks']], ['Site Survey Report'])
        self.assertEqual(result['selection_summary']['requirements_found'], 1)
        self.assertEqual(result['selection_summary']['requirements_not_individually_materialized'], 1)
        self.assertFalse(result['selection_summary']['ai_used'])

    def test_narrative_fallback_filters_contract_terms_and_keeps_real_work(self):
        statements = [
            'The word shall defines a mandatory requirement.',
            'In case of conflict the order of precedence shall be final.',
            'The contractor shall provide all services required for completion.',
            'The contractor shall perform site surveys and collect existing equipment data.',
            'The contractor shall prepare the cable routing layouts.',
            'The contractor shall not perform demolition work.',
        ]
        text = '\n'.join(statements)
        result = extract_requirement_activities([self.source(text)], [self.fact(text, value, i) for i, value in enumerate(statements)])
        self.assertEqual([task['title'] for task in result['tasks']], [
            'Perform site surveys and collect existing equipment data', 'Prepare the cable routing layouts',
        ])
        self.assertEqual([task['task_type'] for task in result['tasks']], ['task', 'deliverable'])

    def test_stale_or_missing_offsets_cannot_supply_work(self):
        text = 'The contractor shall prepare the design report.'
        stale = self.fact(text, text)
        stale['source_locator']['extracted_text_sha256'] = 'old-version'
        unlocated = {**self.fact(text, text, 2), 'source_locator': {}}
        result = extract_requirement_activities([self.source(text)], [stale, unlocated])
        self.assertFalse(result['tasks'])
        self.assertEqual(result['selection_summary']['excluded_reasons']['missing_or_stale_source_locator'], 2)

    def test_same_title_in_distinct_source_lists_remains_separate(self):
        sources = [self.source('Deliverables\n1 Site Survey Report\n', 7),
                   self.source('Required deliverables\n1 Site Survey Report\n', 8)]
        result = extract_requirement_activities(sources)
        self.assertEqual(len(result['tasks']), 2)
        self.assertEqual([row['source_file_id'] for row in result['tasks']], [7, 8])
        self.assertNotEqual(result['tasks'][0]['id'], result['tasks'][1]['id'])
        self.assertEqual(result['tasks'][0]['id'], extract_requirement_activities(sources)['tasks'][0]['id'])

    def test_repeated_source_numbers_and_titles_are_not_renumbered_or_merged(self):
        text = ('Deliverables\nInstrumentation\n12 Cable List.\n12 Cable List.\n'
                '13 Cable List;\nGeneral\n1 Design Report\nConceptual Design Study\n1 Design Report\n'
                'HSE\n1 Design Report\nLoss Prevention\n1 Design Report\n')
        rows = extract_requirement_activities([self.source(text)])['tasks']
        self.assertEqual(len(rows), 7)
        self.assertEqual([row['source_item_number'] for row in rows[:3]], ['12', '12', '13'])
        self.assertEqual([row['title'] for row in rows[:3]], ['Cable List.', 'Cable List.', 'Cable List;'])
        self.assertEqual([row['source_heading'] for row in rows[3:]],
                         ['General', 'Conceptual Design Study', 'HSE', 'Loss Prevention'])
        self.assertEqual(len({row['id'] for row in rows}), 7)
        self.assertEqual([row['source_locator']['source_item_number'] for row in rows[:3]], ['12', '12', '13'])

    def test_conditional_scope_is_visible_and_titles_are_not_catalogue_expanded(self):
        text = 'Deliverables\nGeneral\n1 Development of representative model (if required)\n2 Custom launch checklist\n'
        tasks = extract_requirement_activities([self.source(text)])['tasks']
        self.assertEqual(len(tasks), 2)
        self.assertIn('conditional_scope', tasks[0]['review_flags'])
        self.assertEqual(tasks[1]['title'], 'Custom launch checklist')

    def test_table_of_contents_and_reference_terms_are_not_deliverables(self):
        text = ('APPENDIX 3 – FEED DELIVERABLES.............58\n1 Existing facility\n'
                'The deliverables shall comply with the contract.\n1 Equipment specification\n')
        self.assertFalse(extract_requirement_activities([self.source(text)])['tasks'])

    def test_pdf_header_variations_do_not_extend_last_deliverable(self):
        text = ('PROJECT HEADING\nCompany Doc. No.123\nPage: 1\nDeliverables\n'
                '1 First report\fPROJECT HEADING\nCompany Doc. No.123\nPage: 2\n'
                '2 Second report\fPROJECT HEADING\nCompany Doc. No. 123\nPage: 3\n'
                'Please consider below mentioned\n1. This list is tentative.\n')
        self.assertEqual([row['title'] for row in extract_requirement_activities([self.source(text)])['tasks']],
                         ['First report', 'Second report'])
