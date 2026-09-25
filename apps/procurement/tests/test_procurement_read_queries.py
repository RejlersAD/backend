"""Register display work stays bounded without caching approval decisions."""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.hr_core.models import EmployeeMaster
from apps.onboarding.models import OnboardingRecord
from apps.procurement.models import PurchaseOrder, PurchaseRequisition, Vendor
from apps.procurement.models_master import Budget, Project
from apps.procurement.serializers import PurchaseOrderSerializer, PurchaseRequisitionSerializer
from apps.procurement.views import PurchaseOrderViewSet, PurchaseRequisitionViewSet


class ProcurementReadQueryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.canonical = User.objects.create_user('query-canonical', email='canonical@example.test', first_name='Account', last_name='Name')
        self.onboarded = User.objects.create_user('query-onboarded', email='onboarded@example.test')
        self.fallback = User.objects.create_user('admin', email='case.fallback@example.test')
        self.employee = EmployeeMaster(
            user=self.canonical, employee_number='QUERY-EMP', employee_code='QUERY-EMP', emp_code='QUERY-EMP',
            email=self.canonical.email,
            first_name='Canonical', preferred_given_name='Preferred', last_name='Employee',
            join_date=date(2025, 1, 1), employment_status='active',
        )
        # Preserve differing legacy account names to test HR display precedence.
        EmployeeMaster.objects.bulk_create([self.employee])
        OnboardingRecord.objects.bulk_create([
            OnboardingRecord(user=self.onboarded, employee_name=name, employee_email=f'query-{index}@example.test',
                             position='Engineer', department='Engineering', joining_date=date(2025, 1, 1),
                             target_completion_date=date(2025, 1, 2))
            for index, name in enumerate(('Older Onboarding Name', 'Current Onboarding Name'))
        ])
        OnboardingRecord.objects.filter(employee_email='query-0@example.test').update(updated_at=timezone.now() - timedelta(days=1))
        self.vendor = Vendor.objects.create(vendor_code='QUERY-SUPPLIER', name='Query supplier')
        self.project = Project.objects.create(project_number='QUERY-PROJECT', project_name='Query project')
        self.budget = Budget.objects.create(project=self.project, category='other', description='Reviewed allocation', allocated_amount=1000)

    def requisitions(self, count=10):
        return PurchaseRequisition.objects.bulk_create([
            PurchaseRequisition(
                pr_number=f'RAD-PRJ-PR-{index + 1:04d}_2026', issued_by=self.canonical,
                requested_by=self.onboarded, approved_by=self.fallback, pm_name=self.canonical,
                eng_manager_name=self.onboarded, manager_projects_name=self.fallback, vp_op_name=self.canonical,
                vendor=self.vendor, price_remarks_data={'signed_document_verification': {'signed_off': True}},
                approval_workflow_config=[
                    # Email, not a stale numeric user ID, controls display identity.
                    {'level': 1, 'role': 'Level 1 Approver', 'user_id': str(self.fallback.pk),
                     'user_email': self.canonical.email, 'status': 'pending'},
                    {'level': 2, 'role': 'Engineering', 'user_id': str(self.fallback.pk),
                     'user_email': self.canonical.email, 'status': 'approved'},
                ],
            ) for index in range(count)
        ])

    def test_order_list_joins_display_relations_and_batches_receiving_evidence(self):
        requisitions = self.requisitions()
        PurchaseOrder.objects.bulk_create([
            PurchaseOrder(po_number=f'RAD-PRJ-PUR-{index + 1:04d}_2026', vendor=self.vendor,
                          pr_reference=pr, created_by=self.canonical, approved_by=self.canonical,
                          project=self.project, budget_allocation=self.budget,
                          title='Query purchase', total_amount=100)
            for index, pr in enumerate(requisitions)
        ])
        for count in (1, 10):
            # The register includes receiving/lifecycle capabilities, so receipt
            # evidence is prefetched once for the whole page, never once per PO.
            with self.subTest(rows=count), self.assertNumQueries(2):
                data = PurchaseOrderSerializer(PurchaseOrderViewSet.queryset.all()[:count], many=True).data
            self.assertEqual(len(data), count)
            self.assertEqual(data[0]['created_by_name'], 'Account Name')
            self.assertEqual(data[0]['approved_by_user_name'], 'Account Name')
            self.assertEqual(data[0]['budget_allocation_display'], 'Reviewed allocation')
            self.assertEqual(data[0]['project_name'], 'Query project')
            self.assertEqual(data[0]['vendor_name'], 'Query supplier')

    def test_pr_names_are_batched_across_the_page_with_existing_identity_precedence(self):
        self.requisitions()
        for count in (1, 10):
            with self.subTest(rows=count), CaptureQueriesContext(connection) as queries:
                data = PurchaseRequisitionSerializer(PurchaseRequisitionViewSet.queryset.all()[:count], many=True).data
            # Resolve the canonical Level 0 display reference once per page;
            # all existing name lookups must remain batched as the page grows.
            self.assertEqual(len(queries), 6)
            self.assertEqual(sum('FROM "hr_employee_master"' in query['sql'] for query in queries), 1)
            self.assertEqual(sum('FROM "onboarding_record"' in query['sql'] for query in queries), 1)
            self.assertEqual(len(data), count)
            for row in data:
                self.assertEqual(row['issued_by_name'], 'Preferred Employee')
                self.assertEqual(row['requested_by_name'], 'Current Onboarding Name')
                self.assertEqual(row['requester_name'], row['requested_by_name'])
                self.assertEqual(row['approved_by_name'], 'Case Fallback')
                self.assertEqual(row['pm_name_display'], 'Preferred Employee')
                self.assertEqual(row['eng_manager_name_display'], 'Current Onboarding Name')
                self.assertEqual(row['manager_projects_name_display'], 'Case Fallback')
                self.assertEqual(row['vp_op_name_display'], 'Preferred Employee')
                self.assertIsNone(row['default_level_zero_approver'])
                pending, recorded = row['approval_workflow_config']
                self.assertEqual(pending['user_name'], 'Preferred Employee')
                self.assertEqual(pending['user_id'], str(self.canonical.pk))
                self.assertEqual(recorded['user_name'], 'Preferred Employee')
                self.assertEqual(recorded['user_id'], str(self.fallback.pk))

    def test_display_snapshot_ends_after_each_representation_and_requester_falls_back(self):
        pr = self.requisitions(1)[0]
        pr.requested_by = None
        serializer = PurchaseRequisitionSerializer()
        first = serializer.to_representation(pr)
        self.assertEqual(first['requester_name'], 'Preferred Employee')
        EmployeeMaster.objects.filter(pk=self.employee.pk).update(preferred_given_name='Renamed')
        second = serializer.to_representation(pr)
        self.assertEqual(second['issued_by_name'], 'Renamed Employee')
        self.assertEqual(second['approval_workflow_config'][0]['user_name'], 'Renamed Employee')
