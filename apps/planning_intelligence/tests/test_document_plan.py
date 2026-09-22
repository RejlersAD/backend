"""Evidence-only plans stay independent of engineering templates and vocabulary."""
from copy import deepcopy
from django.test import SimpleTestCase

from ..services.document_plan import build_document_plan, simple_tasks


def upload(text, identifier=1, **overrides):
    return {'id': identifier, 'filename': 'input.txt', 'category': 'other',
            'parse_status': 'done', 'text': text, **overrides}


class DocumentPlanTests(SimpleTestCase):
    def test_withheld_reordered_tsv_matches_domain_facts_without_guessing_missing_semantics(self):
        # Constructed after the adapters were implemented. No parser changes
        # accompany this fixture: unfamiliar work, reversed row order, reordered
        # columns, and extra commercial/witness columns exercise the boundary.
        pipe = (
            'ID|Task|Duration|Duration Unit|Start|Finish|Predecessors|Activity Type\n'
            'P21|Bid-bond authenticity attestation|3|working days|2037-04-06|2037-04-08|None|Task\n'
            'C44|Helium recovery skid purge witness|2|calendar days|2037-04-09|2037-04-10|P21:FS+0d|Task\n'
            'C58|Cryogenic transfer acceptance dossier||||||\n'
        )
        tsv = (
            'Finish Date\tCommercial Lot\tActivity Type\tTask Name\tDepends On\tDuration Units\tTask ID\tStart Date\tOriginal Duration\tWitness authority\n'
            '\tLOT-39\t\tCryogenic transfer acceptance dossier\t\t\tC58\t\t\tTo be agreed\n'
            '2037-04-10\tLOT-39\tTask\tHelium recovery skid purge witness\tP21:FS+0d\tcalendar days\tC44\t2037-04-09\t2\tUnconfirmed third party\n'
            '2037-04-08\tLOT-12\tTask\tBid-bond authenticity attestation\tNone\tworking days\tP21\t2037-04-06\t3\tUnconfirmed bank\n'
        )

        def domain(plan):
            identifiers = {row['id']: row['source_activity_id'] for row in plan['activities']}
            return {row['source_activity_id']: {
                'title': row['name'], 'duration': row['duration_days'], 'duration_unit': row['duration_unit'],
                'start': row['start_date'], 'finish': row['finish_date'], 'is_milestone': row['is_milestone'],
                'dependency_status': row['dependency_status'], 'missing_fields': row['missing_fields'],
                'predecessors': [(identifiers[link['id']], link['type'], link['lag_days'], link['lag_unit'])
                                 for link in row['predecessors']],
            } for row in plan['activities']}

        established = build_document_plan([upload(pipe)], project_id=61)
        withheld = build_document_plan([upload(tsv, 52, filename='commissioning-commercial.tsv')], project_id=61)
        self.assertEqual(len(withheld['activities']), 3)
        self.assertEqual(domain(withheld), domain(established))
        self.assertEqual(domain(withheld)['C44']['predecessors'], [('P21', 'FS', 0, 'days')])
        self.assertEqual(domain(withheld)['P21']['duration_unit'], 'working_days')
        self.assertEqual(domain(withheld)['C44']['duration_unit'], 'calendar_days')
        unknown = next(row for row in withheld['activities'] if row['source_activity_id'] == 'C58')
        self.assertIsNone(unknown['duration_days'])
        self.assertIsNone(unknown['start_date'])
        self.assertIsNone(unknown['finish_date'])
        self.assertIsNone(unknown['is_milestone'])
        self.assertIsNone(unknown['responsible_role'])
        self.assertIn('dependencies', unknown['missing_fields'])
        self.assertTrue(all(not row['calendar_verified'] for row in withheld['activities']))
        self.assertFalse(withheld['ready_for_calculation'])
        self.assertFalse(withheld['calculation_available'])

    def test_document_rows_are_not_expanded_into_default_engineering_stages(self):
        text = ('Task ID|Task|Duration (working days)|Predecessor\n'
                'C1|Inspect concrete|4|None\nC2|Closeout dossier|2|C1:FS+0d\n')
        plan = build_document_plan([upload(text)], project_name='Bridge repair', project_id=82)
        self.assertEqual([row['name'] for row in plan['activities']], ['Inspect concrete', 'Closeout dossier'])
        self.assertEqual([row['duration_days'] for row in plan['activities']], [4, 2])
        self.assertEqual(len(plan['logic_matrix']), 1)
        self.assertEqual(plan['applied_dependency_rules'], [])
        self.assertFalse(plan['calculation_available'])
        self.assertEqual(plan['wbs'][0]['basis'], 'project_record_container')

    def test_template_names_and_phase_words_do_not_create_duration_dates_or_links(self):
        text = ('Task|Duration (days)\n'
                'Mobilization|\nSite survey|\nDesign review|\nPackage closeout|\n')
        plan = build_document_plan([upload(text, filename='Survey_before_design_then_closeout.csv')])
        self.assertEqual(len(plan['activities']), 4)
        for activity in plan['activities']:
            self.assertIsNone(activity['duration_days'])
            self.assertIsNone(activity['start_date'])
            self.assertIsNone(activity['finish_date'])
            self.assertEqual(activity['predecessors'], [])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertTrue(all(item['status'] == 'Not Specified' for item in plan['missing_information']))

    def test_procurement_hours_and_weeks_remain_source_values_without_day_conversion(self):
        text = 'ID,Description,Duration\nP1,Vendor quotation,12 hours\nP2,Manufacture pump,8 weeks\n'
        plan = build_document_plan([upload(text)])
        self.assertEqual([row['source_values']['duration']['unit'] for row in plan['activities']], ['hours', 'weeks'])
        self.assertTrue(all(row['duration_days'] is None for row in plan['activities']))
        self.assertEqual(plan['logic_matrix'], [])

    def test_printed_calendar_day_facts_do_not_run_workday_cpm(self):
        text = 'Task|Duration (calendar days)|Start|Finish\nAcceptance window|7|2036-10-01|2036-10-07\n'
        plan = build_document_plan([upload(text)])
        activity = plan['activities'][0]
        self.assertEqual(activity['duration_days'], 7)
        self.assertEqual(activity['duration_unit'], 'calendar_days')
        self.assertEqual(activity['finish_date'], '2036-10-07')
        self.assertFalse(activity['calendar_verified'])
        self.assertIsNone(activity['total_float_days'])
        self.assertFalse(plan['ready_for_calculation'])

    def test_source_start_and_finish_milestones_keep_their_printed_types_in_draft(self):
        text = ('ID|Task|Duration (days)|Start|Finish|Activity Type\n'
                'M1|Project start|0|2026-01-06||Start Milestone\n'
                'M2|Project finish|0||2026-09-04|Finish Milestone\n')
        plan = build_document_plan([upload(text)])
        tasks = simple_tasks(plan)
        self.assertEqual([row['activity_type'] for row in tasks], ['start_milestone', 'finish_milestone'])
        self.assertEqual([row['is_milestone'] for row in tasks], [True, True])

    def test_predecessor_without_type_and_lag_is_unresolved_without_fs_zero_defaults(self):
        text = 'ID|Task|Duration (days)|Predecessor\nA|A test|1|None\nB|B test|2|A\n'
        plan = build_document_plan([upload(text)])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertEqual(len(plan['unresolved_relationships']), 1)
        link = plan['unresolved_relationships'][0]
        self.assertIsNone(link['type'])
        self.assertIsNone(link['lag'])
        self.assertIn('dependency_details', plan['activities'][1]['missing_fields'])

    def test_explicit_type_without_explicit_lag_remains_unresolved(self):
        text = 'ID|Task|Duration (days)|Predecessor\nA|Run tests|1|None\nB|Release|2|A:FS\n'
        plan = build_document_plan([upload(text)])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertEqual(plan['unresolved_relationships'][0]['type'], 'FS')
        self.assertIsNone(plan['unresolved_relationships'][0]['lag'])

    def test_unsupported_lag_units_do_not_silently_convert(self):
        text = 'ID|Task|Duration (days)|Predecessor\nA|Pour concrete|1|None\nB|Remove forms|2|A:FS+48hours\n'
        plan = build_document_plan([upload(text)])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertEqual(plan['unresolved_relationships'][0]['lag']['unit'], 'hours')

    def test_predecessor_identity_is_scoped_to_source_file(self):
        first = upload('ID|Task|Duration (days)|Predecessor\nA|Supplier approval|2|None\n')
        second = upload('ID|Task|Duration (days)|Predecessor\nB|Factory test|2|A:FS+0d\n', 2)
        plan = build_document_plan([first, second])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertEqual(len(plan['unresolved_relationships']), 1)

    def test_duplicate_predecessor_ids_in_one_source_are_not_arbitrarily_selected(self):
        text = ('ID|Task|Duration (days)|Predecessor\nA|Inspect one|1|None\n'
                'A|Inspect two|1|None\nB|Release|2|A:FS+0d\n')
        plan = build_document_plan([upload(text)])
        self.assertEqual(len(plan['activities']), 3)
        self.assertEqual(plan['logic_matrix'], [])
        self.assertEqual(len(plan['unresolved_relationships']), 1)

    def test_input_repetition_and_file_order_do_not_change_internal_row_identities(self):
        files = [upload('ID|Task|Duration (days)\nD1|Deliver spare|2\n'),
                 upload('ID|Task|Duration (days)\nT1|Training|1\n', 2)]
        before = deepcopy(files)
        first = build_document_plan(files)
        second = build_document_plan(list(reversed(files)))
        self.assertEqual({row['id'] for row in first['activities']}, {row['id'] for row in second['activities']})
        self.assertEqual(files, before)

    def test_source_revision_changes_create_new_traceable_activity_identities(self):
        original = 'ID|Task|Duration (days)\nD1|Deliver spare|2\nT1|Training|1\n'
        amended = 'ID|Task|Duration (days)\nT1|On-site training|2\nD1|Deliver spare|2\n'
        before = build_document_plan([upload(original)])
        after = build_document_plan([upload(amended)])
        self.assertNotEqual({row['source_activity_id']: row['id'] for row in before['activities']},
                         {row['source_activity_id']: row['id'] for row in after['activities']})

    def test_partial_schedule_keeps_entire_register_inventory_without_title_join(self):
        register = upload('Document Number|Document Title|Department\nD1|Inspection|Quality\nD2|Training|Operations\n')
        schedule = upload('ID|Task|Duration (days)\nD1|Inspection|4\n', 2)
        plan = build_document_plan([register, schedule])
        self.assertEqual(len(plan['activities']), 1)
        self.assertEqual(len(plan['register_inventory']), 2)
        self.assertEqual(plan['unmapped_register_count'], 2)
        self.assertTrue(all(row['schedule_association_status'] == 'not_specified' for row in plan['register_inventory']))
        self.assertTrue(any(row['code'] == 'register_schedule_association_not_specified' for row in plan['validation']))

    def test_register_and_schedule_views_of_same_source_row_are_associated_once(self):
        text = 'Document Number|Document Title|Duration (days)\nD1|Inspection|4\n'
        plan = build_document_plan([upload(text)])
        self.assertEqual(len(plan['activities']), 1)
        self.assertEqual(len(plan['register_inventory']), 1)
        self.assertEqual(plan['register_inventory'][0]['schedule_association_status'], 'same_source_row')
        self.assertEqual(plan['unmapped_register_count'], 0)

    def test_deleted_unparsed_or_example_registers_are_not_used_as_plan_sources(self):
        text = 'Document Number|Document Title\nD1|Inspection\n'
        files = [upload(text, is_deleted=True), upload(text, 2, parse_status='pending'),
                 upload(text, 3, category='output_schedule_sample')]
        plan = build_document_plan(files)
        self.assertEqual(plan['activities'], [])
        self.assertEqual(plan['register_inventory'], [])

    def test_register_only_has_exact_rows_without_guessed_five_stages(self):
        text = ('--- Sheet: Public infrastructure ---\n'
                'Document Number|Document Title|Department\n'
                'INF-01|Traffic diversion permit|Construction\nINF-02|Site induction record|Operations\n')
        plan = build_document_plan([upload(text)])
        self.assertEqual([row['name'] for row in plan['activities']], ['Traffic diversion permit', 'Site induction record'])
        self.assertTrue(all(row['duration_days'] is None for row in plan['activities']))
        self.assertEqual(plan['logic_matrix'], [])

    def test_explicit_milestone_keeps_missing_duration_and_original_date_fact(self):
        text = 'ID|Task|Activity Type|Duration (days)|Milestone Date\nM1|Operational readiness|Milestone||2039-01-25\n'
        plan = build_document_plan([upload(text)])
        activity = plan['activities'][0]
        self.assertTrue(activity['is_milestone'])
        self.assertIsNone(activity['duration_days'])
        self.assertEqual(activity['source_values']['milestone_date'], '2039-01-25')
        task = simple_tasks(plan)[0]
        self.assertIsNone(task['duration_days'])
        self.assertEqual(task['evidence_policy'], 'document_driven')

    def test_every_applied_duration_and_link_has_exact_document_evidence(self):
        text = 'ID|Task|Duration (days)|Predecessor\nA|Inspect|1|None\nB|Approve|2|A:FS+0d\n'
        plan = build_document_plan([upload(text)])
        second = plan['activities'][1]
        reference = second['source_references'][0]
        self.assertEqual(reference['locator']['line'], 3)
        self.assertEqual(reference['excerpt'], 'B|Approve|2|A:FS+0d\n')
        self.assertEqual(second['field_evidence']['duration']['raw_text'], '2')
        self.assertEqual(plan['logic_matrix'][0]['source_excerpt'], 'A:FS+0d')
        self.assertEqual(plan['logic_matrix'][0]['lag_unit'], 'days')
        projected = simple_tasks(plan)[1]
        self.assertEqual(projected['evidence_entity_id'], second['id'])
        self.assertEqual(projected['dependency_details'][0]['lag_unit'], 'days')

    def test_unsupported_document_never_falls_back_to_project_template(self):
        plan = build_document_plan([upload('Repair the terminal and complete work safely.')], project_name='Terminal renewal')
        self.assertEqual(plan['activities'], [])
        self.assertEqual(plan['logic_matrix'], [])
        self.assertTrue(any(issue['code'] == 'source_activities_not_specified' for issue in plan['validation']))
        self.assertFalse(plan['extraction_reports'][0]['complete_document_understanding'])
