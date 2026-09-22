from copy import deepcopy
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from ..models import ActivityAssignment, PlanningProject, ScheduleBaseline, ScheduleResource
from ..services.planning_boundaries import freeze_schedule_inputs
from .test_scheduling_engine import ScheduleAPIFixture


class ResourcePlanningTests(ScheduleAPIFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.activity_row = self.activity('A-100', 3)
        self.root = '/api/v1/planning-intelligence/'

    def resource(self, **overrides):
        return ScheduleResource.objects.create(project=self.project, code='CREW', name='Concrete crew',
            unit='crew-hour', capacity_units_per_day=8, **overrides)

    def test_resource_types_and_explicit_productivity_units(self):
        for kind in ('labor', 'equipment', 'material'):
            response = self.client.post(self.root + 'resources/', {'project': self.project.pk, 'code': kind,
                'name': kind, 'resource_type': kind, 'unit': 'hour', 'capacity_units_per_day': '8',
                'productivity_rate': '2.5000', 'productivity_unit': 'm3'}, format='json')
            self.assertEqual(response.status_code, 201, response.data)
            self.assertEqual(response.data['productivity_rate'], '2.5000')
            self.assertNotIn('unit_cost', response.data)
        row = self.resource()
        response = self.client.patch(self.root + f'resources/{row.pk}/',
            {'productivity_rate': '0.0001', 'productivity_unit': 'm3'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row.refresh_from_db()
        row.full_clean()
        for payload in ({'productivity_unit': ''}, {'productivity_rate': '0', 'productivity_unit': 'm3'},
                        {'capacity_units_per_day': '-1'}, {'unit_cost': '99'}):
            response = self.client.patch(self.root + f'resources/{row.pk}/', payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)

    def test_allocation_derives_units_without_rewriting_duration_or_planned_effort(self):
        resource = self.resource(productivity_rate=Decimal('3'), productivity_unit='m3')
        response = self.client.post(self.root + 'assignments/', {'activity': self.activity_row.pk,
            'resource': resource.pk, 'planned_units': '4', 'planned_output_quantity': '10'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['required_units'], '3.34')
        self.assertEqual(response.data['planned_units'], '4.00')
        self.assertNotIn('budgeted_cost', response.data)
        self.activity_row.refresh_from_db()
        self.assertEqual(self.activity_row.duration_days, Decimal('3'))
        response = self.client.patch(self.root + f'resources/{resource.pk}/', {'productivity_unit': 'tonne'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.delete(self.root + f'resources/{resource.pk}/')
        self.assertEqual(response.status_code, 400)

    def test_unknown_rate_and_output_remain_unknown_and_negative_allocations_reject(self):
        resource = self.resource()
        response = self.client.post(self.root + 'assignments/', {'activity': self.activity_row.pk,
            'resource': resource.pk, 'planned_units': '4'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['required_units'])
        self.assertIsNone(response.data['planned_output_quantity'])
        for payload in ({'planned_units': '-1'}, {'planned_output_quantity': '1'}, {'budgeted_hours': '-2'}):
            result = self.client.patch(self.root + f"assignments/{response.data['id']}/", payload, format='json')
            self.assertEqual(result.status_code, 400, result.data)

    def test_project_isolation_and_immutable_assignment_cannot_be_moved(self):
        resource = self.resource()
        other = PlanningProject.objects.create(name='Other', created_by=self.outsider)
        foreign = ScheduleResource.objects.create(project=other, code='OTHER', name='Other')
        response = self.client.post(self.root + 'assignments/', {'activity': self.activity_row.pk,
            'resource': foreign.pk, 'planned_units': '1'}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.get(self.root + 'resources/plan/', {'project': other.pk})
        self.assertEqual(response.status_code, 404)
        allocation = ActivityAssignment.objects.create(activity=self.activity_row, resource=resource)
        self.version.status = 'baselined'
        self.version.save(update_fields=['status'])
        for endpoint, payload in ((f'resources/{resource.pk}/', {'name': 'Changed'}),
                                  (f'assignments/{allocation.pk}/', {'planned_units': '9'})):
            response = self.client.patch(self.root + endpoint, payload, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        response = self.client.delete(self.root + f'assignments/{allocation.pk}/')
        self.assertEqual(response.status_code, 400)

    def test_baseline_resource_plan_uses_frozen_inputs_and_masks_money(self):
        resource = self.resource(productivity_rate=Decimal('2'), productivity_unit='m3', unit_cost=99)
        ActivityAssignment.objects.create(activity=self.activity_row, resource=resource,
            planned_units=5, planned_output_quantity=10, budgeted_cost=495)
        inputs = freeze_schedule_inputs(self.version)
        snapshot = {'accepted_inputs': inputs, 'activities': [{'id': self.activity_row.pk, 'external_id': 'A-100', 'name': 'A-100'}]}
        baseline = ScheduleBaseline.objects.create(schedule=self.schedule, source_version=self.version,
            name='Original', snapshot=snapshot, approved_at=timezone.now(), approved_by=self.owner)
        expected = deepcopy(baseline.snapshot)
        ScheduleResource.objects.filter(pk=resource.pk).update(productivity_rate=9, name='Changed outside API')
        response = self.client.get(self.root + 'resources/plan/', {'project': self.project.pk, 'version': self.version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['basis'], 'approved_baseline')
        self.assertEqual(response.data['resources'][0]['productivity_rate'], '2.0000')
        self.assertEqual(response.data['assignments'][0]['required_units'], '5.00')
        self.assertNotIn('unit_cost', response.data['resources'][0])
        self.assertNotIn('budgeted_cost', response.data['assignments'][0])
        self.assertFalse(response.data['permissions']['can_allocate'])
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, expected)

    def test_resource_edit_invalidates_calculated_version_and_unknown_output_survives(self):
        resource = self.resource()
        ActivityAssignment.objects.create(activity=self.activity_row, resource=resource, planned_units=8)
        self.version.status = 'calculated'
        self.version.calculated_at = timezone.now()
        self.version.save(update_fields=['status', 'calculated_at'])
        response = self.client.patch(self.root + f'resources/{resource.pk}/', {'capacity_units_per_day': '4'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.version.refresh_from_db()
        self.assertEqual(self.version.status, 'draft')
        self.assertIsNone(self.version.calculated_at)

    def test_version_revision_preserves_planned_output_and_resource_basis(self):
        from ..services.master_schedule import _clone
        resource = self.resource(productivity_rate=Decimal('2'), productivity_unit='m3')
        ActivityAssignment.objects.create(activity=self.activity_row, resource=resource,
            planned_units=5, planned_output_quantity=10)
        clone = _clone(self.version, self.owner)
        allocation = ActivityAssignment.objects.get(activity__version=clone)
        self.assertEqual(allocation.planned_output_quantity, Decimal('10'))
        self.assertEqual(allocation.planned_units, Decimal('5'))
        self.assertEqual(allocation.resource_id, resource.pk)
        self.assertEqual(allocation.resource.productivity_rate, Decimal('2'))

    def test_resource_plan_rejects_invalid_identifiers(self):
        for params in ({'project': 'invalid'}, {'project': self.project.pk, 'version': 'invalid'}):
            response = self.client.get(self.root + 'resources/plan/', params)
            self.assertEqual(response.status_code, 400, response.data)
