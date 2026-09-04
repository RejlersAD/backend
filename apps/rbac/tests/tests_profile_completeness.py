from django.test import SimpleTestCase

from apps.rbac.profile_config import get_profile_completeness


class ProfileCompletenessTests(SimpleTestCase):
    def complete_profile(self):
        return {
            'first_name': 'Amina',
            'last_name': 'Hassan',
            'profile_photo': True,
            'phone': '+971500000000',
            'location': 'Abu Dhabi',
            'bio': 'Project engineer',
            'department': 'Engineering',
            'job_title': 'Project Engineer',
            'engineer_profile': {
                'expertise_level': 'mid',
                'years_experience': 0,
                'engineering_disciplines': ['Project Controls'],
                'technical_skills': [{'name': 'Primavera P6', 'proficiency': 4}],
                'certifications': [{'name': 'PMP'}],
                'availability_status': 'available',
                'languages': ['English'],
            },
        }

    def test_complete_profile_is_one_hundred_percent(self):
        result = get_profile_completeness(self.complete_profile())
        self.assertEqual(result['percentage'], 100)
        self.assertTrue(result['is_complete'])
        self.assertEqual(result['missing_fields'], [])

    def test_missing_fields_are_actionable_and_weighted(self):
        profile = self.complete_profile()
        profile['phone'] = ''
        profile['engineer_profile']['technical_skills'] = []
        result = get_profile_completeness(profile)
        self.assertEqual(result['percentage'], 85)
        self.assertEqual(
            {(item['key'], item['section']) for item in result['missing_fields']},
            {
                ('phone', 'Personal information'),
                ('technical_skills', 'Skills & certifications'),
            },
        )
