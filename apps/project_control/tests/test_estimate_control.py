"""Estimate version immutability and authoritative line totals."""
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from openpyxl import Workbook
from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User
from apps.procurement.tests.approval_fixtures import grant_approval, set_position
from ..models import Estimate, EstimateLineItem, ProjectDocument
from ..views import EstimateViewSet


@override_settings(ROOT_URLCONF='config.urls_test')
class EstimateControlAPITests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='estimate-owner', email='estimate-owner@example.test')
        grant_approval(self.owner, 'project_control')
        set_position(self.owner)
        self.viewer = User.objects.create_user(username='estimate-viewer', email='estimate-viewer@example.test')
        self.outsider = User.objects.create_user(username='estimate-outsider', email='estimate-outsider@example.test')
        self.project = Project.objects.create(code='EST-CONTROL', name='Estimate project', owner=self.owner, currency='USD')
        self.other = Project.objects.create(code='EST-OTHER', name='Other project', owner=self.outsider)
        ProjectMember.objects.create(project=self.project, user=self.viewer, role='viewer')
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = '/api/v1/project-control/'

    def create(self, **data):
        return self.client.post(self.base + 'estimates/', {'project': self.project.pk, 'title': 'Working estimate', 'currency': 'USD', **data}, format='json')

    def line(self, estimate_id, **data):
        return self.client.post(self.base + 'estimate-line-items/', {'estimate': estimate_id, 'description': 'Engineering', 'quantity': '2.5000', 'unit_rate': '4.2500', **data}, format='json')

    def test_blank_creation_allocates_kind_version_and_ignores_client_total_status(self):
        first = self.create(version=900, total_amount=900, status='approved')
        second = self.create()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['version'], 1)
        self.assertEqual(second.data['version'], 2)
        self.assertEqual(first.data['status'], 'draft')
        self.assertEqual(first.data['total_amount'], '0.00')
        self.assertEqual(first.data['created_by'], self.owner.pk)
        self.assertTrue(first.data['can_edit'])
        estimate = Estimate.objects.get(pk=second.data['id'])
        estimate.soft_delete()
        self.assertEqual(self.create().data['version'], 3)

    def test_line_creation_computes_decimal_total_and_reconciles_parent(self):
        estimate = self.create().data
        row = self.line(estimate['id'])
        self.assertEqual(row.status_code, 201, row.data)
        self.assertEqual(row.data['line_total'], '10.62')
        parent = Estimate.objects.get(pk=estimate['id'])
        self.assertEqual(parent.total_amount, Decimal('10.62'))
        changed = self.client.patch(self.base + f"estimate-line-items/{row.data['id']}/", {'quantity': '3'}, format='json')
        self.assertEqual(changed.data['line_total'], '12.75')
        parent.refresh_from_db()
        self.assertEqual(parent.total_amount, Decimal('12.75'))

    def test_explicit_zero_and_imported_override_survive_description_edits(self):
        estimate = self.create().data
        zero = self.line(estimate['id'], line_total='0')
        self.assertEqual(zero.data['line_total'], '0.00')
        overridden = self.line(estimate['id'], line_total='200.00')
        patched = self.client.patch(self.base + f"estimate-line-items/{overridden.data['id']}/", {'description': 'Reviewed description'}, format='json')
        self.assertEqual(patched.data['line_total'], '200.00')
        self.assertEqual(Estimate.objects.get(pk=estimate['id']).total_amount, Decimal('200.00'))

    def test_delete_line_is_soft_and_recalculates_active_total_and_detail(self):
        estimate = self.create().data
        line = self.line(estimate['id'])
        deleted = self.client.delete(self.base + f"estimate-line-items/{line.data['id']}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertTrue(EstimateLineItem.objects.get(pk=line.data['id']).is_deleted)
        detail = self.client.get(self.base + f"estimates/{estimate['id']}/").data
        self.assertEqual(detail['line_items'], [])
        self.assertEqual(detail['total_amount'], '0.00')
        self.assertEqual(detail['line_item_count'], 0)

    def test_approval_requires_lines_and_freezes_estimate_and_all_line_mutations(self):
        estimate = self.create().data
        path = self.base + f"estimates/{estimate['id']}/"
        self.assertEqual(self.client.post(path + 'approve/').status_code, 400)
        row = self.line(estimate['id'], line_total='0')
        approved = self.client.post(path + 'approve/')
        self.assertEqual(approved.status_code, 200, approved.data)
        self.assertEqual(approved.data['status'], 'approved')
        self.assertFalse(approved.data['can_edit'])
        self.assertFalse(approved.data['can_approve'])
        self.assertTrue(approved.data['can_copy'])
        self.assertEqual(self.client.patch(path, {'title': 'Bypass'}, format='json').status_code, 400)
        self.assertEqual(self.client.delete(path).status_code, 400)
        self.assertEqual(self.line(estimate['id']).status_code, 400)
        line_path = self.base + f"estimate-line-items/{row.data['id']}/"
        self.assertEqual(self.client.patch(line_path, {'line_total': 900}, format='json').status_code, 400)
        self.assertEqual(self.client.delete(line_path).status_code, 400)

    def test_copy_version_keeps_kind_currency_notes_and_active_amounts_without_approval(self):
        estimate = self.create(kind='revised', notes='Basis: vendor quotations. Qualification: freight excluded.').data
        self.line(estimate['id'], line_total='500.00')
        deleted = self.line(estimate['id'], line_total='99.00')
        self.client.delete(self.base + f"estimate-line-items/{deleted.data['id']}/")
        self.client.post(self.base + f"estimates/{estimate['id']}/approve/")
        copied = self.client.post(self.base + f"estimates/{estimate['id']}/copy-version/", {'title': 'Next review'}, format='json')
        self.assertEqual(copied.status_code, 201, copied.data)
        self.assertEqual(copied.data['version'], 2)
        self.assertEqual(copied.data['status'], 'draft')
        self.assertEqual(copied.data['kind'], 'revised')
        self.assertEqual(copied.data['currency'], 'USD')
        self.assertEqual(copied.data['notes'], estimate['notes'])
        self.assertEqual(copied.data['total_amount'], '500.00')
        self.assertEqual(copied.data['line_item_count'], 1)

    def test_list_capabilities_and_superseded_lock_are_consistent(self):
        estimate = self.create().data
        self.line(estimate['id'])
        path = self.base + f"estimates/{estimate['id']}/"
        response = self.client.post(path + 'supersede/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['can_edit'])
        self.assertEqual(self.client.post(path + 'approve/').status_code, 400)
        self.assertEqual(self.line(estimate['id']).status_code, 400)
        listed = self.client.get(self.base + 'estimates/', {'project': self.project.pk}).data
        row = (listed['results'] if isinstance(listed, dict) else listed)[0]
        self.assertEqual(row['line_item_count'], 1)
        self.assertNotIn('line_items', row)
        self.assertFalse(row['can_approve'])
        self.assertTrue(row['can_copy'])

    def test_copy_preserves_summary_only_sales_award_until_cost_lines_are_added(self):
        award = Estimate.objects.create(
            project=self.project, kind='awarded', status='approved', currency='USD',
            title='Recorded Sales award', total_amount='1235000.00',
            notes='Created during approved Sales handover. Award reference: PO-7788',
        )
        copied = self.client.post(self.base + f'estimates/{award.pk}/copy-version/')
        self.assertEqual(copied.status_code, 201, copied.data)
        self.assertEqual(copied.data['total_amount'], '1235000.00')
        self.assertEqual(copied.data['line_items'], [])
        self.assertEqual(copied.data['status'], 'draft')
        self.assertEqual(copied.data['notes'], award.notes)
        self.assertEqual(self.client.post(self.base + f"estimates/{copied.data['id']}/approve/").status_code, 400)
        self.line(copied.data['id'], line_total='100.00')
        self.assertEqual(Estimate.objects.get(pk=copied.data['id']).total_amount, Decimal('100.00'))

    def test_project_kind_and_line_associations_cannot_be_reassigned(self):
        first = self.create().data
        second = self.create().data
        path = self.base + f"estimates/{first['id']}/"
        self.assertEqual(self.client.patch(path, {'project': self.other.pk}, format='json').status_code, 400)
        self.assertEqual(self.client.patch(path, {'kind': 'awarded'}, format='json').status_code, 400)
        line = self.line(first['id']).data
        self.assertEqual(self.client.patch(self.base + f"estimate-line-items/{line['id']}/", {'estimate': second['id']}, format='json').status_code, 400)

    def test_source_document_is_project_scoped_and_line_source_is_read_only(self):
        document = ProjectDocument.objects.create(project=self.other, title='Other project BOQ', file='other/boq.xlsx')
        self.assertEqual(self.create(source_document=document.pk).status_code, 400)
        estimate = self.create().data
        row = self.line(estimate['id'], source_row={'fake': 'changed provenance'})
        self.assertEqual(row.data['source_row'], {})

    def test_copy_rejects_legacy_source_document_from_another_project(self):
        document = ProjectDocument.objects.create(project=self.other, title='Unrelated source', file='other/boq.xlsx')
        estimate = Estimate.objects.create(project=self.project, source_document=document)
        response = self.client.post(self.base + f'estimates/{estimate.pk}/copy-version/')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Estimate.objects.filter(project=self.project).count(), 1)

    def test_locked_actions_recheck_soft_deletion_after_initial_lookup(self):
        original_get_object = EstimateViewSet.get_object

        def deleted_after_lookup(view):
            instance = original_get_object(view)
            Estimate.objects.filter(pk=instance.pk).update(is_deleted=True)
            return instance

        for action in ('copy-version', 'approve', 'supersede'):
            with self.subTest(action=action):
                estimate = self.create().data
                with patch.object(EstimateViewSet, 'get_object', deleted_after_lookup):
                    response = self.client.post(self.base + f"estimates/{estimate['id']}/{action}/")
                self.assertEqual(response.status_code, 404)

    def test_legacy_variance_keeps_valid_comparisons_and_rejects_foreign_sources(self):
        base = self.create().data
        current = self.create().data
        self.line(base['id'], quantity='1', unit_rate='100', wbs_code='01')
        self.line(current['id'], quantity='1', unit_rate='125', wbs_code='01')
        path = self.base + 'analytics/estimate-variance/'
        params = {'project': self.project.pk, 'base': base['id'], 'compare': current['id']}
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(Decimal(response.data['totals']['delta']), Decimal('25'))
        foreign = Estimate.objects.create(project=self.other, total_amount='900000')
        for field in ('base', 'compare'):
            self.assertEqual(self.client.get(path, {**params, field: foreign.pk}).status_code, 404)
        self.assertEqual(self.client.get(path, {**params, 'base': 'invalid'}).status_code, 400)

    def test_viewer_cannot_create_edit_copy_approve_or_import(self):
        estimate = self.create().data
        self.line(estimate['id'])
        self.client.force_authenticate(self.viewer)
        path = self.base + f"estimates/{estimate['id']}/"
        self.assertFalse(self.client.get(path).data['can_edit'])
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.client.patch(path, {'title': 'Bypass'}, format='json').status_code, 403)
        self.assertEqual(self.client.post(path + 'copy-version/').status_code, 403)
        self.assertEqual(self.client.post(path + 'approve/').status_code, 403)
        upload = SimpleUploadedFile('boq.xlsx', b'not parsed for unauthorized user')
        response = self.client.post(self.base + 'documents/import-boq/', {'project': self.project.pk, 'file': upload}, format='multipart')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(ProjectDocument.objects.count(), 0)

    def test_import_keeps_explicit_zero_currency_and_deleted_version_sequence(self):
        from ..services.excel_import import import_boq_excel
        old = Estimate.objects.create(project=self.project, kind='estimate', version=5)
        old.soft_delete()
        workbook = Workbook()
        worksheet = workbook.active
        worksheet.append(['WBS', 'Description', 'Quantity', 'Unit Rate', 'Amount'])
        worksheet.append(['01', 'Included item', 10, 20, 0])
        worksheet.append(['02', 'Calculated item', 2, 15, None])
        output = BytesIO()
        workbook.save(output)
        output.seek(0)
        summary = import_boq_excel(project=self.project, file_obj=output, user=self.owner)
        self.assertEqual(summary['version'], 6)
        self.assertEqual(summary['currency'], 'USD')
        self.assertEqual(Decimal(summary['total_amount']), Decimal('30'))
        estimate = Estimate.objects.get(pk=summary['estimate_id'])
        self.assertEqual(list(estimate.line_items.values_list('line_total', flat=True)), [Decimal('0'), Decimal('30')])
