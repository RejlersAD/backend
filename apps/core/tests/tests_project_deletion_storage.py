"""Committed deletion manifests clean only unreferenced, owned storage objects."""
from copy import deepcopy
from io import StringIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import transaction
from django.db.models.deletion import Collector
from django.test import TransactionTestCase

from apps.core.project_deletion_storage import collect_project_files, cleanup_project_files
from apps.core.project_models import Project
from apps.planning_intelligence.models import PlanningFile, PlanningProject
from apps.project_control.models import ProjectDocument
from apps.rbac.models import AuditLog
# Release-test URL checks otherwise import these FileField models only AFTER
# SQLite model synchronization. Register them before this suite builds tables.
from apps.spec_customization import matching_models  # noqa: F401


class ProjectDeletionStorageTests(TransactionTestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix='project-deletion-storage-tests-')
        self.addCleanup(self.directory.cleanup)
        self.storage = FileSystemStorage(location=self.directory.name)
        for model in (PlanningFile, ProjectDocument):
            patched = patch.object(model._meta.get_field('file'), 'storage', self.storage)
            patched.start()
            self.addCleanup(patched.stop)
        self.project = Project.objects.create(code='DELETE-FILES', name='Synthetic deletion test')
        self.workspace = PlanningProject.objects.create(enterprise_project=self.project)

    def source(self, name='owned/source.txt', **extra):
        self.storage.save(name, ContentFile(b'Synthetic test content'))
        return PlanningFile.objects.create(project=self.workspace, file=name, parse_status='done', **extra)

    def deletion(self, *, delete=True):
        collector = Collector(using='default')
        collector.collect([self.project])
        files = collect_project_files(collector)
        audit = AuditLog.objects.create(
            user_email='owner@example.test', action='delete', resource_type='Project',
            resource_repr=self.project.code, success=True,
            changes={'after': {'permanently_deleted': True}},
            metadata={'command': 'permanently_delete_project', 'project_id': self.project.pk,
                      'planning_workspace_ids': [self.workspace.pk], 'files': files},
        )
        if delete:
            collector.delete()
        return audit

    def events(self, audit):
        return AuditLog.objects.filter(action='file_delete', resource_id=audit.pk)

    def test_full_collector_manifest_includes_archived_and_fast_delete_file_rows(self):
        self.source(is_deleted=True)
        self.storage.save('owned/control.txt', ContentFile(b'Synthetic document'))
        ProjectDocument.objects.create(project=self.project, file='owned/control.txt', is_deleted=True)
        collector = Collector(using='default')
        collector.collect([self.project])
        # Signal registrations can change Django's fast-delete choice. Exercise
        # that collector representation explicitly as well as the real closure.
        fast = Collector(using='default')
        fast.fast_deletes.append(ProjectDocument.objects.filter(project=self.project))
        self.assertEqual(collect_project_files(fast)[0]['name'], 'owned/control.txt')
        manifest = collect_project_files(collector)
        self.assertEqual({(row['model'], row['field'], row['name']) for row in manifest}, {
            ('planning_intelligence.PlanningFile', 'file', 'owned/source.txt'),
            ('project_control.ProjectDocument', 'file', 'owned/control.txt'),
        })
        self.assertTrue(all(len(row['storage_fingerprint']) == 64 for row in manifest))

    def test_cleanup_deletes_bytes_and_retry_does_not_repeat_effects_or_mutate_manifest(self):
        self.source()
        audit = self.deletion()
        manifest = deepcopy(audit.metadata)
        with patch.object(self.storage, 'delete', wraps=self.storage.delete) as deletion:
            result = cleanup_project_files(audit.pk)
            retry = cleanup_project_files(audit.pk)
        self.assertTrue(result['completed'], result)
        self.assertEqual(result['deleted'], 1)
        self.assertEqual(retry, result)
        deletion.assert_called_once_with('owned/source.txt')
        self.assertFalse(self.storage.exists('owned/source.txt'))
        self.assertEqual(self.events(audit).count(), 1)
        audit.refresh_from_db()
        self.assertEqual(audit.metadata, manifest)

    def test_surviving_archived_cross_model_reference_preserves_shared_bytes(self):
        self.source()
        other = Project.objects.create(code='SURVIVOR', name='Other project')
        ProjectDocument.objects.create(project=other, file='owned/source.txt', is_deleted=True)
        audit = self.deletion()
        with patch.object(self.storage, 'delete') as deletion:
            result = cleanup_project_files(audit.pk)
        deletion.assert_not_called()
        self.assertTrue(result['completed'])
        self.assertEqual(result['preserved_shared'], 1)
        self.assertTrue(self.storage.exists('owned/source.txt'))

    def test_storage_failure_is_durable_redacted_and_management_command_can_retry(self):
        self.source()
        audit = self.deletion()
        with patch.object(self.storage, 'delete', side_effect=OSError('provider-secret-token')):
            result = cleanup_project_files(audit.pk)
        self.assertFalse(result['completed'])
        self.assertEqual(result['remaining'], 1)
        failure = self.events(audit).get()
        self.assertFalse(failure.success)
        self.assertNotIn('provider-secret-token', str(failure.metadata) + failure.error_message)
        self.assertTrue(self.storage.exists('owned/source.txt'))
        output = StringIO()
        call_command('retry_project_file_cleanup', audit_id=str(audit.pk), stdout=output)
        self.assertIn('pending: 0', output.getvalue())
        self.assertEqual(self.events(audit).filter(success=True).count(), 1)
        self.assertFalse(self.storage.exists('owned/source.txt'))

    def test_reference_check_failure_preserves_bytes_and_remains_retryable(self):
        self.source()
        audit = self.deletion()
        with patch('apps.core.project_deletion_storage._surviving_reference', side_effect=RuntimeError('Unavailable')):
            result = cleanup_project_files(audit.pk)
        self.assertEqual(result['failures'][0]['code'], 'reference_check_failed')
        self.assertTrue(self.storage.exists('owned/source.txt'))
        self.assertTrue(cleanup_project_files(audit.pk)['completed'])

    def test_missing_bytes_are_successful_without_delete(self):
        self.source()
        audit = self.deletion()
        self.storage.delete('owned/source.txt')
        with patch.object(self.storage, 'delete') as deletion:
            result = cleanup_project_files(audit.pk)
        deletion.assert_not_called()
        self.assertTrue(result['completed'])
        self.assertEqual(result['already_absent'], 1)

    def test_retry_after_audit_write_failure_records_absence_without_redeleting(self):
        self.source()
        audit = self.deletion()
        with patch('apps.core.project_deletion_storage._record', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaisesMessage(RuntimeError, 'Audit unavailable'):
                cleanup_project_files(audit.pk)
        self.assertTrue(AuditLog.objects.filter(pk=audit.pk).exists())
        self.assertFalse(self.events(audit).exists())
        self.assertFalse(self.storage.exists('owned/source.txt'))
        with patch.object(self.storage, 'delete') as deletion:
            result = cleanup_project_files(audit.pk)
        deletion.assert_not_called()
        self.assertTrue(result['completed'])
        self.assertEqual(result['already_absent'], 1)
        self.assertEqual(self.events(audit).filter(success=True).count(), 1)

    def test_storage_delete_that_leaves_bytes_is_not_reported_as_success(self):
        self.source()
        audit = self.deletion()
        with patch.object(self.storage, 'delete'):
            result = cleanup_project_files(audit.pk)
        self.assertFalse(result['completed'])
        self.assertEqual(result['remaining'], 1)
        self.assertTrue(self.storage.exists('owned/source.txt'))
        self.assertFalse(self.events(audit).get().success)

    def test_storage_configuration_change_requires_review_before_retry(self):
        self.source()
        audit = self.deletion()
        with TemporaryDirectory(prefix='other-project-storage-tests-') as other:
            with patch.object(PlanningFile._meta.get_field('file'), 'storage', FileSystemStorage(location=other)):
                result = cleanup_project_files(audit.pk)
        self.assertEqual(result['failures'][0]['code'], 'storage_configuration_changed')
        self.assertTrue(self.storage.exists('owned/source.txt'))

    def test_live_project_or_active_transaction_cannot_trigger_storage_deletion(self):
        self.source()
        audit = self.deletion(delete=False)
        with self.assertRaisesMessage(ValueError, 'still exists'):
            cleanup_project_files(audit.pk)
        with transaction.atomic():
            with self.assertRaisesMessage(ValueError, 'committed deletion'):
                cleanup_project_files(audit.pk)
        self.assertFalse(self.events(audit).exists())
        self.assertTrue(self.storage.exists('owned/source.txt'))

    def test_invalid_path_manifest_is_rejected_without_storage_calls(self):
        self.source()
        audit = self.deletion()
        audit.metadata['files'][0]['name'] = '../unrelated.txt'
        audit.save(update_fields=['metadata'])  # Corrupted fixture, not cleanup behavior.
        with patch.object(self.storage, 'delete') as deletion:
            result = cleanup_project_files(audit.pk)
        deletion.assert_not_called()
        self.assertEqual(result['failures'][0]['code'], 'invalid_manifest_entry')

    def test_command_failure_reports_pending_and_rejects_unrelated_audit(self):
        self.source()
        audit = self.deletion()
        with patch.object(self.storage, 'delete', side_effect=OSError('Unavailable')):
            with self.assertRaisesMessage(CommandError, 'remains pending'):
                call_command('retry_project_file_cleanup', audit_id=str(audit.pk), stdout=StringIO())
        other = AuditLog.objects.create(user_email='', action='read', resource_type='Project')
        with self.assertRaises(CommandError):
            call_command('retry_project_file_cleanup', audit_id=str(other.pk), stdout=StringIO())
