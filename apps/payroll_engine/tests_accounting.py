from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from .models import PayrollRun, PayrollAccountingExport
from .views import PayrollRunViewSet


class AccountingGenerationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username='journal-admin', email='admin@example.com', is_superuser=True)
        self.run = PayrollRun.objects.create(year=2026, month=9, total_gross=1000,
                                            total_deductions=100, total_net=900)

    def generate(self, user):
        request = APIRequestFactory().post('/accounting-export/', {'target_system': 'generic'}, format='json')
        force_authenticate(request, user=user)
        return PayrollRunViewSet.as_view({'post': 'accounting_export'})(request, pk=self.run.pk)

    def test_generate_before_finance_approval_without_changing_workflow(self):
        original_status = self.run.status
        self.assertEqual(self.generate(self.admin).status_code, 201)
        journal = PayrollAccountingExport.objects.get(run=self.run)
        self.assertEqual(journal.total_debit, Decimal('1000'))
        self.assertEqual(journal.total_credit, journal.total_debit)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, original_status)
        self.assertIsNone(self.run.finance_approved_by_id)
        self.assertEqual(self.generate(self.admin).status_code, 201)
        self.assertEqual(PayrollAccountingExport.objects.filter(run=self.run).count(), 1)

    def test_employee_cannot_generate(self):
        employee = get_user_model().objects.create_user(username='journal-employee', email='employee@example.com')
        self.assertEqual(self.generate(employee).status_code, 403)
        self.assertFalse(PayrollAccountingExport.objects.exists())
