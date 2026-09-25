"""Exact source-register rows survive intelligence, confirmation and WBS setup."""
from collections import Counter
from copy import deepcopy
from datetime import date
import io
from unittest.mock import patch

from django.test import TestCase
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User

from ..models import PlanningFile, PlanningProject
from ..services.document_intelligence import compile_run_intelligence, run_document_intelligence
from ..services.parsers import _extract_xlsx
from ..services.preview_confirmation import confirmation_is_current
from ..services.schedule_basis import _deliverable_rows, build_schedule_basis
from ..services.work_breakdown import _initial_tasks


class CaptionedRegisterWorkBreakdownTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='captioned-register')
        self.project = PlanningProject.objects.create(name='Captioned register', created_by=self.owner)
        self.source = PlanningFile.objects.create(
            project=self.project, category='sow', file='tests/synthetic-captioned.pdf',
            original_filename='Synthetic scope.pdf', parse_status='done', uploaded_by=self.owner,
            extracted_text=('Table 4: FEED Deliverables\nS. No. Description\nSupport Services\n'
                            '1 Clear source report\nTitle for retained\n2\nstructures\n'
                            '3 Clear source dossier\n3 Clear source dossier\n'),
        )

    def test_register_scope_and_group_survive_without_ai_or_discipline_inference(self):
        run, preview = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertEqual(preview['register_summary']['row_count'], 4)
        self.assertEqual(set(preview['disciplines']), {'not_specified'})
        facts = list(run.facts.filter(fact_type='deliverable').order_by('id'))
        self.assertEqual(len(facts), 4)
        self.assertTrue(all(fact.value['source_group'] == 'Support Services' for fact in facts))
        self.assertTrue(all(fact.source_locator['source_group'] == 'Support Services' for fact in facts))
        self.assertEqual(preview['disciplines']['not_specified']['register_rows'][0]['source_group'], 'Support Services')
        self.assertEqual(facts[2].value['register_item'], facts[3].value['register_item'])
        self.assertNotEqual(facts[2].source_locator['line'], facts[3].source_locator['line'])

    def test_matrix_confirmation_cannot_promote_unmarked_conditional_or_ambiguous_scope(self):
        from ..services.document_plan import project_document_plan
        self.source.extracted_text = ('Table 2: Applicable Deliverables for Work Packages\n'
            'S. No. Discipline Document / Deliverable Description Work Packages Remarks\n'
            '4.2 General\n4.2.1 General Clear dossier X\n'
            '4.2.2 General Unmarked drawing\n4.2.3 General Conditional report X If required\n'
            '4.2.4 General Last dossier X\n')
        self.source.save(update_fields=['extracted_text'])
        run, preview = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertEqual(preview['register_summary']['row_count'], 4)
        facts = list(run.facts.filter(fact_type='deliverable').order_by('id'))
        self.assertEqual([fact.value['applicability_status'] for fact in facts], ['marked', 'not_marked', 'conditional', 'marked'])
        self.assertEqual(facts[2].value['source_remarks'], 'If required')
        self.assertEqual(facts[2].source_locator['applicability_status'], 'conditional')
        run.facts.filter(fact_type='deliverable').update(status='confirmed')
        reviewed = _deliverable_rows(run, preview)
        self.assertEqual(sum(row['requires_source_review'] for row in reviewed), 2)
        self.assertEqual([row['title'] for row in _initial_tasks(run, preview)], ['Clear dossier', 'Last dossier'])
        plan = project_document_plan(self.project, preview)
        self.assertEqual(len(plan['register_inventory']), 4)
        self.assertEqual([row['name'] for row in plan['activities']], ['Clear dossier', 'Last dossier'])
        basis = build_schedule_basis(run)
        self.assertEqual(basis.deliverables.filter(status='needs_review').count(), 2)

    def test_bulk_confirmation_does_not_promote_ambiguous_register_title_to_wbs_or_plan(self):
        from ..services.document_plan import project_document_plan
        run, preview = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        run.facts.filter(fact_type='deliverable').update(status='confirmed')
        rows = _deliverable_rows(run, preview)
        ambiguous = [row for row in rows if row.get('requires_source_review')]
        self.assertEqual(len(ambiguous), 1)
        self.assertFalse(ambiguous[0]['confirmed'])
        self.assertFalse(ambiguous[0]['excluded'])
        tasks = _initial_tasks(run, preview)
        self.assertEqual(len(tasks), 3)
        self.assertNotIn('Title for retained structures', [row['title'] for row in tasks])
        plan = project_document_plan(self.project, preview)
        self.assertEqual(len(plan['register_inventory']), 4)
        self.assertEqual(len(plan['activities']), 3)
        self.assertEqual(next(row for row in plan['validation'] if row['code'] == 'register_title_boundary_ambiguous')['severity'], 'error')
        basis = build_schedule_basis(run)
        self.assertEqual(basis.deliverables.filter(status='needs_review', canonical_name='Title for retained structures').count(), 1)


class RegisterWorkBreakdownTests(TestCase):
    GROUP_COUNTS = {
        'GENERAL': 34, 'HSE': 81, 'INSTRUMENTATION': 41,
        'ELECTRICAL': 24, 'CIVIL': 38, 'HVAC': 2,
    }
    DUPLICATE_TITLE = 'FIRE & GAS DETECTION ADEQUACY REPORT - AREA A'

    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(username='register-planner', email='register@example.com', password='test')
        cls.project = PlanningProject.objects.create(
            name='Register planning test', created_by=cls.owner,
            effective_date=date(2026, 10, 5), planned_end_date=date(2026, 12, 20),
        )
        organization = Organization.objects.create(name='Register tests', code='register-tests')
        role = Role.objects.create(name='Register planner', code='register-planner', level=4)
        module, _ = Module.objects.get_or_create(code='planning_package', defaults={'name': 'Planning'})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.create(role=role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'update', 'create'], is_active=True):
            RolePermission.objects.create(role=role, permission=permission)
        profile, _ = UserProfile.objects.get_or_create(user=cls.owner, defaults={'organization': organization})
        UserRole.objects.create(user_profile=profile, role=role)

        # Reproduce the reported workbook shape and discipline counts using
        # synthetic deliverables rather than committing a client's workbook.
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Sheet1'
        sheet.append([])
        sheet.append([])
        sheet.append([None, 'SL. NO.', 'DISCIPLINE', 'DRAWING / DOCUMENT (TITLE)'])
        cls.expected = []
        cls.expected_by_code = {}
        for label, count in cls.GROUP_COUNTS.items():
            sheet.append([None, None, label, None])
            code = label.lower()
            cls.expected_by_code[code] = []
            for index in range(count):
                title = f'{label} PROJECT DOCUMENT {index + 1} (+/- 15%)'
                if label == 'GENERAL' and index < 3:
                    title = f'INTERNAL QUALITY AUDIT REPORT @{(index + 1) * 30}% OF ENGINEERING COMPLETION'
                elif label == 'ELECTRICAL' and index < 2:
                    title = f'CABLE SCHEDULE - FAR-{index * 6}'
                elif label == 'HSE' and index in (0, 1):
                    title = cls.DUPLICATE_TITLE
                elif label == 'HVAC':
                    title = f'HVAC ADEQUEACY REPORT -FAR-{index * 6}'
                serial = len(cls.expected) + 1
                cls.expected.append((serial, code, title))
                cls.expected_by_code[code].append(title)
                sheet.append([None, serial, label, title])
        stream = io.BytesIO()
        workbook.save(stream)
        stream.seek(0)
        extracted = _extract_xlsx(stream)
        stream.close()
        workbook.close()
        cls.file = PlanningFile.objects.create(
            project=cls.project, category='mdr', file='tests/generated-register.xlsx',
            original_filename='Generated MDR.xlsx', extracted_text=extracted,
            size_bytes=len(extracted), parse_status='done', uploaded_by=cls.owner,
        )
        cls.sow = PlanningFile.objects.create(
            project=cls.project, category='sow', file='tests/generated-scope.txt',
            original_filename='Scope.txt', size_bytes=100, parse_status='done', uploaded_by=cls.owner,
            extracted_text='Scope of work. Piping Material Specification, Process Flow Diagram and HAZOP.',
        )
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None), \
                patch('apps.planning_intelligence.services.claude_client.call_claude') as ai_call:
            cls.intelligence_run, cls.preview = run_document_intelligence(cls.project, user=cls.owner)
        ai_call.assert_not_called()

    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.confirm_url = f'/api/v1/planning-intelligence/intelligence-runs/{self.intelligence_run.pk}/confirm-preview/'
        self.wbs_url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/work-breakdown/'

    def confirm(self, preview=None):
        response = self.client.post(self.confirm_url, {'preview': preview or self.preview}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.intelligence_run.refresh_from_db()
        self.assertTrue(confirmation_is_current(self.intelligence_run))
        return response

    def test_preview_matches_all_220_source_rows_and_contains_no_catalogue_additions(self):
        self.assertEqual(self.intelligence_run.status, 'succeeded')
        self.assertEqual(self.preview['deliverable_source'], 'register')
        self.assertEqual(self.preview['register_summary']['row_count'], 220)
        self.assertEqual(set(self.preview['disciplines']), set(self.expected_by_code))
        for code, expected in self.expected_by_code.items():
            group = self.preview['disciplines'][code]
            self.assertEqual(group['name'], code.upper())
            self.assertEqual(group['deliverables'], expected)
            self.assertEqual(group['mentioned_in_source'], expected)
            self.assertEqual(group['ai_discovered'], [])
        facts = list(self.intelligence_run.facts.filter(fact_type='deliverable').order_by('id'))
        self.assertEqual(len(facts), 220)
        self.assertEqual(
            [(fact.value['register_item'], fact.value['discipline'], fact.value['original_title']) for fact in facts],
            self.expected,
        )
        self.assertTrue(all(fact.value['source_register'] for fact in facts))
        self.assertTrue(all(fact.source_file_id == self.file.pk for fact in facts))
        self.assertFalse(self.intelligence_run.facts.filter(fact_type='deliverable', value__name='Piping Material Specification').exists())
        self.assertFalse(self.intelligence_run.facts.filter(fact_type='hse_study').exists())
        self.assertEqual(self.preview['hse_studies'], [])
        self.assertEqual(self.preview['available_hse_studies'], [])

    def test_source_provenance_and_all_rows_survive_initial_work_breakdown(self):
        tasks = _initial_tasks(self.intelligence_run, self.preview)
        self.assertEqual(len(tasks), 220)
        self.assertEqual([(task['discipline'], task['title']) for task in tasks], [row[1:] for row in self.expected])
        self.assertEqual(Counter(task['discipline'] for task in tasks), {
            label.lower(): count for label, count in self.GROUP_COUNTS.items()
        })
        self.assertEqual(len({task['id'] for task in tasks}), 220)
        self.assertEqual(tasks, _initial_tasks(self.intelligence_run, self.preview))
        for serial, task in enumerate(tasks, 1):
            reference = task['source_references'][0]
            self.assertEqual(reference['file_id'], self.file.pk)
            self.assertEqual(reference['locator']['sheet'], 'Sheet1')
            self.assertEqual(reference['locator']['register_item'], serial)
            self.assertIsInstance(reference['locator']['line'], int)
            self.assertIn(task['title'], reference['excerpt'])
        titles = [task['title'] for task in tasks]
        self.assertIn('CABLE SCHEDULE - FAR-0', titles)
        self.assertIn('CABLE SCHEDULE - FAR-6', titles)
        for percent in (30, 60, 90):
            self.assertIn(f'INTERNAL QUALITY AUDIT REPORT @{percent}% OF ENGINEERING COMPLETION', titles)

    def test_header_register_does_not_hide_rows_from_a_second_pdf_register(self):
        title = 'MIXED REGISTER INSPECTION REPORT'
        PlanningFile.objects.create(
            project=self.project, category='mdr', file='tests/legacy-register.pdf',
            original_filename='Additional register.pdf', parse_status='done',
            extracted_text=f'1 CIVIL PJ-ABC-CIV-0001 {title} NEW 1 A',
        )
        with patch('apps.planning_intelligence.services.claude_client.get_claude_config', return_value=None):
            run, preview = run_document_intelligence(self.project, user=self.owner)
        self.assertEqual(preview['register_summary']['row_count'], 221)
        self.assertEqual(preview['register_summary']['source_file_count'], 2)
        self.assertIn(title, preview['disciplines']['civil']['deliverables'])
        tasks = _initial_tasks(run, preview)
        self.assertEqual(len(tasks), 221)
        task = next(task for task in tasks if task['title'] == title)
        self.assertEqual(task['document_number'], 'PJ-ABC-CIV-0001')

    def test_same_title_register_rows_have_distinct_basis_keys_and_task_ids(self):
        self.confirm()
        selected = self.intelligence_run.summary['preview_confirmation']['preview']
        self.assertEqual(selected['disciplines']['hse']['deliverables'].count(self.DUPLICATE_TITLE), 2)
        tasks = [task for task in _initial_tasks(self.intelligence_run, selected) if task['title'] == self.DUPLICATE_TITLE]
        self.assertEqual(len(tasks), 2)
        self.assertNotEqual(tasks[0]['id'], tasks[1]['id'])
        basis = build_schedule_basis(self.intelligence_run)
        duplicates = list(basis.deliverables.filter(original_title=self.DUPLICATE_TITLE))
        self.assertEqual(basis.deliverables.count(), 220)
        self.assertEqual(len(duplicates), 2)
        self.assertNotEqual(duplicates[0].canonical_key, duplicates[1].canonical_key)
        self.assertNotEqual(duplicates[0].source_fact_ids, duplicates[1].source_fact_ids)
        self.assertEqual({item.status for item in duplicates}, {'confirmed'})

    def test_exact_exclusion_does_not_remove_similar_location_or_audit_documents(self):
        preview = deepcopy(self.preview)
        preview['disciplines']['electrical']['excluded_deliverables'] = ['CABLE SCHEDULE - FAR-0']
        audit30 = 'INTERNAL QUALITY AUDIT REPORT @30% OF ENGINEERING COMPLETION'
        preview['disciplines']['general']['excluded_deliverables'] = [audit30]
        rows = _deliverable_rows(self.intelligence_run, preview)
        excluded = [row['original_title'] for row in rows if row['excluded']]
        self.assertCountEqual(excluded, ['CABLE SCHEDULE - FAR-0', audit30])
        tasks = _initial_tasks(self.intelligence_run, preview)
        self.assertEqual(len(tasks), 218)
        titles = [task['title'] for task in tasks]
        self.assertIn('CABLE SCHEDULE - FAR-6', titles)
        self.assertNotIn('CABLE SCHEDULE - FAR-0', titles)
        self.assertNotIn(audit30, titles)
        for percent in (60, 90):
            self.assertIn(f'INTERNAL QUALITY AUDIT REPORT @{percent}% OF ENGINEERING COMPLETION', titles)

    def test_confirm_save_reload_preserves_220_tasks_and_original_group_labels(self):
        self.confirm()
        response = self.client.get(self.wbs_url)
        self.assertEqual(response.status_code, 200, response.data)
        initial = deepcopy(response.data)
        self.assertEqual(len(initial['tasks']), 220)
        labels = {row['code']: row['name'] for row in initial['disciplines']}
        self.assertEqual(labels['hvac'], 'HVAC')
        self.assertEqual(labels['hse'], 'HSE')
        self.assertEqual(labels['general'], 'GENERAL')
        initial['tasks'][0].update(effort_hours=16, acceptance_criteria='Reviewed against the source register')
        response = self.client.put(self.wbs_url, {**initial, 'advance': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.get(self.wbs_url)
        self.assertEqual(response.status_code, 200, response.data)
        restored = response.data
        self.assertEqual(len(restored['tasks']), 220)
        self.assertEqual([task['id'] for task in restored['tasks']], [task['id'] for task in initial['tasks']])
        self.assertEqual([task['title'] for task in restored['tasks']], [row[2] for row in self.expected])
        self.assertEqual(restored['tasks'][0]['effort_hours'], 16)
        self.assertEqual(restored['tasks'][0]['source_references'], initial['tasks'][0]['source_references'])
        self.assertEqual(restored['revision'], 1)
        self.intelligence_run.refresh_from_db()
        reloaded_preview = compile_run_intelligence(self.intelligence_run)
        self.assertEqual(reloaded_preview['disciplines']['hse']['deliverables'].count(self.DUPLICATE_TITLE), 2)
        self.assertFalse(self.project.schedules.exists())
