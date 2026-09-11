from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from apps.rbac.models import EngineerProfile, UserProfile, Organization


class CareerLevelTests(TestCase):
    def setUp(self):
        organization = Organization.objects.create(name='Career testing', code='CAREER')
        user = get_user_model().objects.create_user(username='career-employee', email='career@example.test')
        self.profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
        self.profile.department = 'finance'
        self.profile.save()
        self.client = APIClient()
        self.client.force_authenticate(user)

    def test_corporate_levels_save_and_reload(self):
        for level, _ in EngineerProfile.EXPERTISE_CHOICES:
            with self.subTest(level=level):
                response = self.client.patch('/api/v1/rbac/users/me/', {'engineer_profile': {
                    'expertise_level': level, 'years_experience': 8,
                    'engineering_disciplines': ['Finance & Accounting'],
                    'technical_skills': [{'name': 'Microsoft Excel', 'proficiency': 4}],
                    'languages': ['English'],
                }}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
                saved = EngineerProfile.objects.get(user_profile=self.profile)
                self.assertEqual(saved.expertise_level, level)
                self.assertEqual(saved.engineering_disciplines, ['Finance & Accounting'])
                self.assertEqual(saved.technical_skills[0]['proficiency'], 4)
                loaded = self.client.get('/api/v1/rbac/users/me/?view=profile')
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.data['engineer_profile']['expertise_level'], level)
