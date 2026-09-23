"""Pure workflow contract tests: supplied scope, work sequence and allowances."""
from unittest import TestCase

from ..services.enterprise_workflows import normalize_project_type, select_workflow


class EnterpriseWorkflowTests(TestCase):
    def test_foundation_deliverable_has_real_work_sequence_and_varied_durations(self):
        title = 'Civil Foundation Design'
        workflow = select_workflow(title, project_type='Building Project')
        self.assertEqual(workflow['code'], 'foundation_design')
        stages = workflow['stages']
        self.assertEqual([stage['code'] for stage in stages],
                         ['INPUTS', 'BASIS', 'CALCULATE', 'DRAWINGS', 'CHECK', 'ISSUE'])
        self.assertGreaterEqual(len(stages), 5)
        self.assertGreater(len({stage['duration_days'] for stage in stages}), 1)
        self.assertTrue(all(stage['duration_days'] > 1 for stage in stages))
        self.assertEqual(len({stage['name'] for stage in stages}), len(stages))
        for stage in stages:
            self.assertIn(title, stage['name'])
            self.assertIn(title, stage['deliverable'])
            self.assertTrue(stage['responsible_role'])
            self.assertTrue(stage['acceptance_criteria'])
            self.assertEqual(stage['discipline'], 'Civil / Structural')
        self.assertAlmostEqual(sum(stage['progress_weight'] for stage in stages), 100)

    def test_industry_and_scope_select_expected_workflows(self):
        cases = [
            ('Architectural Floor Plans', 'Building Project', 'architectural_design', 'engineering'),
            ('Structural Steel Design', 'building', 'structural_design', 'engineering'),
            ('MEP Services Design', 'building', 'mep_design', 'engineering'),
            ('Process Flow Diagrams', 'Industrial Project', 'process_design', 'engineering'),
            ('Pump Procurement', 'industrial', 'procurement', 'procurement'),
            ('Equipment Installation', 'industrial', 'construction', 'construction'),
            ('Plant Commissioning', 'industrial', 'commissioning', 'commissioning'),
            ('Topographic Survey', 'Infrastructure Project', 'survey', 'survey'),
            ('Highway Design', 'infrastructure', 'infrastructure_design', 'engineering'),
            ('Utilities Design', 'infrastructure', 'utilities_design', 'engineering'),
            ('Road Construction', 'infrastructure', 'construction', 'construction'),
            ('Pipeline Acceptance Testing', 'infrastructure', 'testing', 'testing'),
            ('Process FEED Package', 'Oil & Gas Project', 'feed', 'feed'),
            ('Pipeline Detailed Design', 'oil_gas', 'piping_design', 'detailed_design'),
            ('Compressor Procurement', 'oil and gas', 'procurement', 'procurement'),
            ('Piping Construction', 'oil_gas', 'construction', 'construction'),
            ('Process Unit Pre-Commissioning', 'oil_gas', 'precommissioning', 'precommissioning'),
        ]
        for title, industry, code, phase in cases:
            with self.subTest(title=title):
                workflow = select_workflow(title, project_type=industry)
                self.assertEqual(workflow['code'], code)
                self.assertEqual(workflow['phase'], phase)
                self.assertGreaterEqual(len(workflow['stages']), 5)
                self.assertTrue(all(title in stage['name'] for stage in workflow['stages']))

    def test_duration_ranges_and_complexity_are_auditable(self):
        cases = [
            ('Foundation Design', 'INPUTS', [2, 5]),
            ('Foundation Design', 'BASIS', [3, 7]),
            ('Pump Procurement', 'EVALUATE', [5, 10]),
            ('Pump Vendor Documentation Review', 'REVIEW', [3, 5]),
            ('Road Construction', 'EXECUTE', [10, 30]),
            ('Plant Commissioning', 'EXECUTE', [5, 15]),
        ]
        for title, stage_code, expected_range in cases:
            durations = []
            for complexity in ('simple', 'standard', 'complex'):
                with self.subTest(title=title, complexity=complexity):
                    workflow = select_workflow(title, complexity=complexity)
                    stage = next(item for item in workflow['stages'] if item['code'] == stage_code)
                    basis = stage['duration_basis']
                    self.assertEqual(basis['range_days'], expected_range)
                    self.assertEqual(basis['complexity'], complexity)
                    self.assertEqual(basis['source'], 'planning_allowance')
                    self.assertEqual(basis['unit'], 'working_days')
                    self.assertTrue(basis['requires_planner_review'])
                    durations.append(stage['duration_days'])
            self.assertEqual(durations[0], expected_range[0])
            self.assertEqual(durations[-1], expected_range[-1])
            self.assertEqual(durations, sorted(durations))

    def test_project_controls_deliverables_have_distinct_workflows(self):
        for title, code in (
            ('Integrated Baseline Schedule', 'schedule_controls'),
            ('Monthly Progress Report', 'progress_controls'),
            ('Project Risk Register', 'risk_controls'),
            ('Design Change Request', 'change_controls'),
            ('Project Capital Cost Estimate', 'estimate_controls'),
        ):
            with self.subTest(title=title):
                workflow = select_workflow(title)
                self.assertEqual(workflow['code'], code)
                self.assertEqual(workflow['discipline'], 'Project Controls')
                self.assertGreaterEqual(len(workflow['stages']), 5)

    def test_execution_documents_do_not_authorize_physical_execution(self):
        for title in ('Piping Construction Drawings', 'Pump Installation Procedure',
                      'Electrical Testing Procedure', 'Plant Commissioning Philosophy',
                      'Compressor Purchase Requisition'):
            with self.subTest(title=title):
                result = select_workflow(title)
                self.assertEqual(result['code'], 'technical_document')
                self.assertNotIn('EXECUTE', [stage['code'] for stage in result['stages']])
        evaluation = select_workflow('Pump Technical Bid Evaluation')
        self.assertEqual(evaluation['code'], 'procurement_evaluation')
        self.assertNotIn('AWARD', [stage['code'] for stage in evaluation['stages']])

    def test_reviews_and_safety_studies_use_the_requested_work(self):
        review = select_workflow('Structural Design Review')
        self.assertEqual(review['code'], 'technical_review')
        self.assertNotIn('DEVELOP', [stage['code'] for stage in review['stages']])
        safety = select_workflow('HAZOP Study')
        self.assertEqual(safety['code'], 'technical_safety')
        self.assertEqual(safety['discipline'], 'Technical Safety')
        mapping = select_workflow('Prepare the Fire and Gas Mapping Report', discipline='general')
        self.assertEqual(mapping['code'], 'technical_safety')
        self.assertEqual(mapping['discipline'], 'Technical Safety')

    def test_common_engineering_deliverables_and_acronyms_remain_scope_bound(self):
        for title, discipline in (
            ('P&IDs', 'Process'), ('PFD', 'Process'),
            ('Single Line Diagram', 'Electrical'), ('SLDs', 'Electrical'),
            ('Cable Schedule', 'Electrical'), ('Load List', 'Electrical'),
            ('Piping Material Specification', 'Piping'),
            ('Instrument Index', 'Instrumentation'),
            ('Equipment Datasheets', 'Mechanical'),
            ('MTO / Bill of Materials', 'Engineering'),
        ):
            with self.subTest(title=title):
                workflow = select_workflow(title)
                self.assertEqual(workflow['discipline'], discipline)
                self.assertTrue(all(title in stage['name'] for stage in workflow['stages']))

    def test_explicit_discipline_supports_actual_document_numbers_and_names(self):
        result = select_workflow('Area C Technical Package P-204', discipline='Electrical')
        self.assertEqual(result['code'], 'electrical_design')
        self.assertEqual(result['discipline'], 'Electrical')
        self.assertTrue(all('Area C Technical Package P-204' in task['deliverable'] for task in result['stages']))

    def test_placeholders_and_ambiguous_scope_do_not_create_invented_tasks(self):
        for title in ('', '  ', 'WBS 1', 'Task A', 'Activity 123', 'Deliverable 1',
                      'Package', 'Phase 2', 'Project', 'General', 'TBD', 'Demo Schedule',
                      'Placeholder Foundation', 'Random Stuff', 'Area A', '123'):
            with self.subTest(title=title):
                with self.assertRaises(ValueError):
                    select_workflow(title, project_type='building')
        with self.assertRaises(ValueError):
            select_workflow('Task A', discipline='Electrical')
        with self.assertRaises(ValueError):
            select_workflow('Ambiguous scope', discipline='not a discipline')

    def test_industry_does_not_add_unrequested_work_packages(self):
        result = select_workflow('Electrical Load List', project_type='building')
        self.assertEqual(result['code'], 'electrical_design')
        self.assertTrue(all('Electrical Load List' in stage['name'] for stage in result['stages']))
        self.assertNotIn('Architectural', repr(result))
        self.assertNotIn('Foundation', repr(result))

    def test_engineering_schedules_are_not_project_baseline_schedules(self):
        for title, code in (
            ('Cable Schedule', 'electrical_design'),
            ('Valve Schedule', 'piping_design'),
            ('Door Schedule', 'architectural_design'),
        ):
            with self.subTest(title=title):
                self.assertEqual(select_workflow(title)['code'], code)
        self.assertEqual(select_workflow('Construction Schedule')['code'], 'schedule_controls')

    def test_return_values_are_independent_and_deterministic(self):
        original = select_workflow('Civil Foundation Design')
        changed = select_workflow('Civil Foundation Design')
        self.assertEqual(original, changed)
        changed['stages'][0]['name'] = 'corrupted'
        changed['stages'][0]['duration_basis']['range_days'][0] = 999
        self.assertEqual(original, select_workflow('Civil Foundation Design'))

    def test_invalid_configuration_is_explicit(self):
        with self.assertRaisesRegex(ValueError, 'Complexity'):
            select_workflow('Civil Design', complexity='magic')
        with self.assertRaisesRegex(ValueError, 'project type'):
            select_workflow('Civil Design', project_type='space')
        self.assertEqual(normalize_project_type('Oil & Gas Project'), 'oil_gas')
        self.assertEqual(normalize_project_type(''), '')
        self.assertEqual(normalize_project_type('engineering'), '')
        self.assertEqual(select_workflow('Structural Steel Design', project_type='engineering')['phase'], 'engineering')
        for project_type in ('software', 'internal', 'business'):
            with self.subTest(project_type=project_type), self.assertRaisesRegex(ValueError, 'project type'):
                select_workflow('Civil Design', project_type=project_type)

    def test_explicit_execution_verbs_preserve_the_source_action(self):
        for title, code in (
            ('Procure process pumps', 'procurement'),
            ('Test installed water mains', 'testing'),
            ('Inspect completed steelwork', 'inspection'),
            ('Energize electrical switchgear', 'commissioning'),
        ):
            with self.subTest(title=title):
                result = select_workflow(title)
                self.assertEqual(result['code'], code)
                self.assertGreaterEqual(len(result['stages']), 5)
