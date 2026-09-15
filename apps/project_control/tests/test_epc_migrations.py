"""Exercise the additive EPC migrations against an isolated pre-EPC schema."""
from importlib import import_module

from django.apps import apps
from django.db import connection
from django.db.migrations.state import ProjectState
from django.test import TransactionTestCase

from apps.core.project_models import Project
from apps.procurement.models import PurchaseRequisition
from apps.users.models import User
from ..epc_models import IntegratedBaseline, RequisitionWBSLink, WBSActivityLink
from ..execution_models import EPCWorkEvent, EPCWorkItem


class EpcMigrationTests(TransactionTestCase):
    def test_0007_and_0008_apply_without_rewriting_existing_projects_or_requisitions(self):
        database_name = connection.settings_dict['NAME']
        self.assertTrue(database_name == ':memory:' or str(database_name).startswith(('test_', 'file:memorydb_')))
        user = User.objects.create_user(username='migration-owner', email='migration-owner@example.test')
        project = Project.objects.create(code='MIGRATION-KEEP', name='Original project', owner=user)
        requisition = PurchaseRequisition.objects.create(pr_number='MIGRATION-PR', title='Original requisition',
            enterprise_project=project, status='approved')
        before_project = (project.pk, project.code, project.name, project.owner_id, project.created_at, project.updated_at)
        before_requisition = (requisition.pk, requisition.title, requisition.status,
                              requisition.enterprise_project_id, requisition.created_at, requisition.updated_at)
        current_models = [IntegratedBaseline, RequisitionWBSLink, WBSActivityLink, EPCWorkItem, EPCWorkEvent]
        before = ProjectState.from_apps(apps)
        for model in reversed(current_models):
            before.remove_model('project_control', model._meta.model_name)

        def drop_epc_tables():
            existing = set(connection.introspection.table_names())
            with connection.schema_editor() as editor:
                for model in reversed(current_models):
                    if model._meta.db_table in existing:
                        editor.delete_model(model)

        try:
            drop_epc_tables()
            state = before
            for name in ['0007_epc_foundation', '0008_epc_execution']:
                migration = import_module(f'apps.project_control.migrations.{name}').Migration(name, 'project_control')
                with connection.schema_editor() as editor:
                    state = migration.apply(state, editor)
            tables = set(connection.introspection.table_names())
            self.assertTrue({model._meta.db_table for model in current_models} <= tables)
            for field in EPCWorkItem._meta.local_many_to_many:
                self.assertIn(field.remote_field.through._meta.db_table, tables)
            self.assertEqual(IntegratedBaseline.objects.count(), 0)
            self.assertEqual(EPCWorkItem.objects.count(), 0)
            project.refresh_from_db()
            requisition.refresh_from_db()
            self.assertEqual((project.pk, project.code, project.name, project.owner_id,
                              project.created_at, project.updated_at), before_project)
            self.assertEqual((requisition.pk, requisition.title, requisition.status,
                              requisition.enterprise_project_id, requisition.created_at, requisition.updated_at), before_requisition)
        finally:
            # Keep the runner usable after either a successful or failed migration.
            drop_epc_tables()
            with connection.schema_editor() as editor:
                for model in current_models:
                    editor.create_model(model)
