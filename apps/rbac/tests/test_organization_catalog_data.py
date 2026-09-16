"""Source fidelity and hierarchy integrity for the organizational catalog."""

from django.test import SimpleTestCase

from apps.rbac.organization_catalog import (
    DEPARTMENTS,
    ORGANIZATIONAL_ROLES,
    get_organization_catalog,
    get_organizational_role_choices,
    resolve_department_code,
)


class OrganizationCatalogDataTests(SimpleTestCase):
    def test_revision_and_named_positions_match_supplied_chart(self):
        catalog = get_organization_catalog()
        self.assertEqual(catalog['source']['document_id'], 'RAD-HM-CHT-0001')
        self.assertEqual(catalog['source']['revision'], 6)
        self.assertEqual(catalog['source']['date'], '2025-12-12')
        self.assertEqual(len(catalog['departments']), 16)
        self.assertEqual(len(catalog['organizational_roles']), 21)
        holders = {role['code']: role['holder_name'] for role in ORGANIZATIONAL_ROLES if role['holder_name']}
        self.assertEqual(holders, {
            'ceo': 'Jarmo Suominen',
            'head_sales_business_development': 'Anam Abbas',
            'head_operations_project_delivery': 'Mohamad El-Ghawanmeh',
            'head_hr_administration': 'Sanglin Samuel',
            'cfo': 'Aleksi Murtomaki',
            'manager_projects': 'Jamal Ayoub',
            'manager_engineering': 'Rafat Saqer',
            'qhse_manager': 'Shaju Chacko',
            'procurement_manager': 'Richa Thomas',
            'senior_manager_project_controls': 'Timothy Dolan',
            'hod_process': 'Debasis Sana',
            'hod_civil_structural': 'Sherwin Mapaye',
            'hod_instrumentation_control': 'Sanu Jacob',
            'hod_electrical': 'Swapnil Linge',
            'hod_piping_mechanical_pipeline': 'Amit Thakur',
        })

    def test_chart_branches_keep_ai_rin_and_project_controls_distinct(self):
        departments = {item['code']: item for item in DEPARTMENTS}
        roles = {item['code']: item for item in ORGANIZATIONAL_ROLES}
        self.assertEqual(departments['radai']['parent_code'], 'operations')
        self.assertIn('RIN Growth', departments['radai']['functions'])
        self.assertIsNone(departments['radai']['head_role_code'])
        self.assertEqual(roles['rin_manager']['reports_to_role_code'], 'manager_engineering')
        self.assertEqual(roles['senior_manager_project_controls']['reports_to_role_code'], 'manager_projects')
        for role in roles.values():
            if role['code'].startswith('hod_') or role['code'] == 'engineering_manager':
                self.assertEqual(role['reports_to_role_code'], 'manager_engineering')

    def test_references_are_unique_valid_and_acyclic(self):
        departments = {item['code']: item for item in DEPARTMENTS}
        roles = {item['code']: item for item in ORGANIZATIONAL_ROLES}
        self.assertEqual(len(departments), len(DEPARTMENTS))
        self.assertEqual(len(roles), len(ORGANIZATIONAL_ROLES))
        for department in departments.values():
            self.assertIn(department['head_role_code'], {None, *roles})
        for role in roles.values():
            self.assertIn(role['department_code'], departments)
            self.assertLessEqual(len(role['label']), 100)
        for entries, parent_key in ((departments, 'parent_code'), (roles, 'reports_to_role_code')):
            for code in entries:
                seen = set()
                current = code
                while current is not None:
                    self.assertIn(current, entries)
                    self.assertNotIn(current, seen, f'Cycle in hierarchy at {code}')
                    seen.add(current)
                    current = entries[current][parent_key]

    def test_acting_positions_and_executive_dual_titles_are_preserved(self):
        roles = {item['code']: item for item in ORGANIZATIONAL_ROLES}
        self.assertEqual({role['code'] for role in roles.values() if role['acting']}, {
            'head_hr_administration', 'hod_piping_mechanical_pipeline',
        })
        self.assertIn('Senior VP, Middle East Region', roles['ceo']['additional_titles'])
        self.assertIn('VP & Head of Finance & ICT', roles['cfo']['additional_titles'])

    def test_aliases_only_filter_choices_and_unknown_values_are_not_invented(self):
        self.assertEqual(resolve_department_code('Human Resources'), 'hr')
        self.assertEqual(resolve_department_code('Instrument & Control'), 'instrument')
        self.assertEqual(resolve_department_code('mechanical'), 'piping')
        self.assertIsNone(resolve_department_code('External legacy unit'))
        self.assertEqual(get_organizational_role_choices('External legacy unit'), [])
        self.assertEqual(get_organizational_role_choices('HR & Administration'), [{
            'value': 'head_hr_administration',
            'label': 'Head of HR & Administration (Acting)',
            'department_code': 'hr',
        }])

    def test_consumers_cannot_mutate_catalog_between_requests(self):
        first = get_organization_catalog()
        first['departments'][0]['label'] = 'Changed'
        first['organizational_roles'][0]['additional_titles'].clear()
        second = get_organization_catalog()
        self.assertEqual(second['departments'][0]['label'], 'Management')
        self.assertEqual(second['organizational_roles'][0]['additional_titles'], ['Senior VP, Middle East Region'])
