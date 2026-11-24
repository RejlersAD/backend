"""The two RADAI organizations: name mapping and the shared organization helper."""
from django.test import TestCase

from apps.rbac.models import Organization
from apps.rbac.organization_entities import ENTITY_CHOICES, RAD, RIN, entity_for, organization_for


class OrganizationEntityTests(TestCase):
    def test_legacy_names_map_to_the_two_codes(self):
        for value, expected in [
            ('Rejlers AB', RAD), ('Abu Dhabi', RAD), ('Default Organization', RAD), ('RAD', RAD),
            ('Rejlers International Engineering Solutions AB', RAD), ('Based in Abu Dhabi', RAD),
            ('Rejlers IN', RIN), ('Rejlers India', RIN), ('rin', RIN), ('REJ_RIN', RIN),
            ('Rejlers International Engineering Solutions IN', RIN), ('Rejlers India (RIN)', RIN),
        ]:
            self.assertEqual(entity_for(value), expected, value)
        self.assertIsNone(entity_for(''))
        self.assertIsNone(entity_for(None))

    def test_choices_are_exactly_two(self):
        self.assertEqual(ENTITY_CHOICES, [('RAD', 'Rejlers Abu Dhabi'), ('RIN', 'Rejlers India (RIN)')])

    def test_api_refuses_a_third_organization(self):
        from django.contrib.auth import get_user_model
        from rest_framework.test import APIRequestFactory, force_authenticate
        from apps.rbac.views import OrganizationViewSet
        admin = get_user_model().objects.create_superuser(username='org-admin', email='org-admin@example.test', password=None)

        def create(payload):
            request = APIRequestFactory().post('/organizations/', payload, format='json')
            force_authenticate(request, admin)
            return OrganizationViewSet.as_view({'post': 'create'})(request)

        refused = create({'name': 'Rejlers Finland', 'code': 'REJ_FI'})
        self.assertEqual(refused.status_code, 400)
        self.assertIn('two organizations', str(refused.data))
        self.assertFalse(Organization.objects.filter(code='REJ_FI').exists())
        allowed = create({'name': 'Rejlers India (RIN)', 'code': 'REJ_RIN'})
        self.assertIn(allowed.status_code, (200, 201), getattr(allowed, 'data', None))

    def test_organization_for_reuses_or_creates_without_a_default_org(self):
        existing = Organization.objects.create(code='REJ_UAE', name='Rejlers Abu Dhabi')
        self.assertEqual(organization_for(RAD), existing)
        india = organization_for(RIN)
        self.assertEqual((india.code, india.name, india.country), ('REJ_RIN', 'Rejlers India (RIN)', 'India'))
        self.assertEqual(organization_for(RIN), india)
        self.assertFalse(Organization.objects.filter(name='Default Organization').exists())
