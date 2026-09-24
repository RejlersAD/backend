"""Real row-lock races on an explicitly isolated PostgreSQL test database.

Run with config.settings_procurement_postgresql_test. TransactionTestCase is
intentional: fixtures commit before independent worker connections start, and
the production on_commit callbacks run on real commits. Only external delivery
tasks are mocked; locking, permissions, validation and database writes are real.
"""

from copy import deepcopy
import json
from threading import Event, Thread, current_thread
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import close_old_connections, connection, connections
from django.test import TransactionTestCase, override_settings
from django.urls import include, path
from rest_framework.fields import empty
from rest_framework.routers import DefaultRouter
from rest_framework.test import APIClient

from apps.notifications.models import Notification
from apps.procurement.models import PurchaseRequisition
from apps.procurement.services.requisition_concurrency import check_requisition_precondition
from apps.procurement.views import PurchaseRequisitionViewSet
from apps.rbac.models import Module, Permission, Role, RoleModule, RolePermission, UserRole
from apps.rbac.module_actions import ensure_module_actions
from apps.rbac.route_guard import secure_module_endpoints

from .approval_fixtures import grant_approval, set_position


router = DefaultRouter()
router.register('requisitions', PurchaseRequisitionViewSet, basename='postgresql-concurrency-requisitions')
urlpatterns = [path('api/v1/procurement/', include(router.urls))]
secure_module_endpoints(urlpatterns)
BASE = '/api/v1/procurement/requisitions/'
WAIT_SECONDS = 12


@skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL connections are required; SQLite cannot verify row locks.')
@override_settings(ROOT_URLCONF=__name__, TEAMS_APPROVAL_WEBHOOK_URL='https://teams.example.test/test-only',
                   WEB_PUSH_VAPID_PRIVATE_KEY='')
class PurchaseRequisitionPostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.tasks = {}
        for key, target in {
            'teams': 'apps.notifications.teams.send_teams_approval_assignment.delay',
            'email': 'apps.notifications.services.send_notification_email.delay',
            'push': 'apps.notifications.services.send_web_push_notification.delay',
        }.items():
            patcher = patch(target)
            self.tasks[key] = patcher.start()
            self.addCleanup(patcher.stop)

        users = get_user_model()
        self.issuer = users.objects.create_user('pg-issuer', email='issuer@pg.example.test')
        self.procurement = users.objects.create_user('pg-procurement', email='procurement@pg.example.test')
        self.alternate = users.objects.create_user('pg-alternate', email='alternate@pg.example.test')
        self.engineer = users.objects.create_user('pg-engineer', email='engineer@pg.example.test')
        for user, title in ((self.issuer, 'Engineer'), (self.procurement, 'Procurement Manager'),
                            (self.alternate, 'Procurement Manager'), (self.engineer, 'Engineer')):
            grant_approval(user, 'procurement_requisitions')
            set_position(user, title)
            profile = user.rbac_profile
            profile.signature_image = 'synthetic-postgresql-signature'
            profile.save(update_fields=['signature_image'])
        module = Module.objects.get(code='procurement_requisitions')
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        self.editor_role = Role.objects.create(code='pg-concurrency-editor', name='Synthetic PostgreSQL editor', level=3)
        RoleModule.objects.create(role=self.editor_role, module=module)
        for permission in module.permissions.filter(action__in=['read', 'create', 'update']):
            RolePermission.objects.create(role=self.editor_role, permission=permission)
        UserRole.objects.create(user_profile=self.issuer.rbac_profile, role=self.editor_role)
        self.workflow = [
            {'level': 0, 'role': 'Procurement Department', 'user_id': str(self.procurement.pk)},
            {'level': 1, 'role': 'Level 1 Approver', 'user_id': str(self.engineer.pk)},
        ]
        self.client = APIClient()
        self.client.force_authenticate(self.issuer)
        created = self.client.post(BASE, {
            'pr_number': 'RAD-PRJ-PR-0900_2026', 'po_applicable': True,
            'requisition_type': 'general', 'product_service': 'Synthetic concurrency scope',
            'total_price': '100.00', 'net_total_excl_vat': '100.00',
            'approval_workflow_config': self.workflow,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.pr = PurchaseRequisition.objects.get(pk=created.data['id'])
        # Exercise the exact server string, including its fractional precision.
        self.token = created.data['updated_at']
        self.original_notification_ids = set(Notification.objects.values_list('pk', flat=True))
        for task in self.tasks.values():
            task.reset_mock()

    def request(self, method, payload, action='', client=None):
        return getattr(client or self.client, method)(f'{BASE}{self.pr.pk}/{action}', payload, format='json')

    def notices(self):
        return Notification.objects.filter(metadata__pr_id=str(self.pr.pk), metadata__event_type='approval_assignment')

    def assert_no_new_notifications(self):
        self.assertEqual(set(Notification.objects.values_list('pk', flat=True)), self.original_notification_ids)
        for task in self.tasks.values():
            task.assert_not_called()

    def snapshot(self):
        return deepcopy({
            'requisition': PurchaseRequisition.objects.filter(pk=self.pr.pk).values().get(),
            'notifications': list(Notification.objects.order_by('pk').values()),
            # Mock _Call objects have custom attribute lookup; deepcopying them
            # can record synthetic __deepcopy__ calls on a chained mock.
            'delivery_calls': {name: tuple(repr(call) for call in task.mock_calls)
                               for name, task in self.tasks.items()},
        })

    def assert_conflict(self, response):
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data['code'], 'stale_requisition')

    def race(self, winner, contender):
        """Hold the first real row lock until PostgreSQL sees the second waiter.

        Each operation is (HTTP method, payload, action suffix). The gate wraps
        the real comparison without replacing it. No timing assumption decides
        the winner: pg_blocking_pids must identify its connection as the blocker
        of the contender's SELECT FOR UPDATE before the gate is released.
        """
        acquired = Event()
        release = Event()
        prepared = {'winner': Event(), 'contender': Event()}
        finished = {'winner': Event(), 'contender': Event()}
        results, failures, pids, isolations = {}, {}, {}, {}
        trace = {'winner': [], 'contender': []}
        in_flight = {'winner': None, 'contender': None}
        table = PurchaseRequisition._meta.db_table

        def record(name, kind):
            db = connections['default']
            with db.cursor() as cursor:
                cursor.execute('SELECT pg_backend_pid(), txid_current()')
                pid, transaction_id = cursor.fetchone()
            trace[name].append({
                'kind': kind, 'pid': pid, 'transaction_id': transaction_id,
                'atomic': db.in_atomic_block, 'autocommit': db.get_autocommit(),
            })

        def checked(requisition, expected_updated_at=empty):
            name = current_thread().name
            if name in trace:
                record(name, 'compare' if expected_updated_at is not empty else 'omitted')
                if name == 'winner' and not acquired.is_set():
                    acquired.set()
                    if not release.wait(WAIT_SECONDS):
                        raise AssertionError('Timed out holding the winner row lock for the observed contender.')
            return check_requisition_precondition(requisition, expected_updated_at)

        def worker(name, operation):
            # Django connection state is thread-local; close explicitly so this
            # request cannot inherit a connection from test setup or a pool.
            close_old_connections()
            db = connections['default']
            db.close()
            try:
                with db.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '20s'")
                    cursor.execute("SET lock_timeout = '15s'")
                    cursor.execute('SELECT pg_backend_pid()')
                    pids[name] = cursor.fetchone()[0]
                    cursor.execute('SHOW transaction_isolation')
                    isolations[name] = cursor.fetchone()[0]
                prepared[name].set()
                client = APIClient()
                client.force_authenticate(get_user_model().objects.get(pk=self.issuer.pk))

                def trace_sql(execute, sql, params, many, context):
                    sql_upper = sql.upper()
                    kind = None
                    if table.upper() in sql_upper:
                        if 'FOR UPDATE' in sql_upper:
                            kind = 'lock'
                        elif sql_upper.lstrip().startswith('UPDATE '):
                            kind = 'mutate'
                    in_flight[name] = kind
                    try:
                        result = execute(sql, params, many, context)
                    finally:
                        in_flight[name] = None
                    if kind:
                        record(name, kind)
                    return result

                with db.execute_wrapper(trace_sql):
                    method, payload, action = operation
                    results[name] = self.request(method, deepcopy(payload), action, client)
            except BaseException as error:
                failures[name] = error
            finally:
                db.close()
                finished[name].set()

        threads = [Thread(target=worker, name=name, args=(name, operation), daemon=True)
                   for name, operation in (('winner', winner), ('contender', contender))]
        try:
            with patch('apps.procurement.serializers.check_requisition_precondition', side_effect=checked), \
                    patch('apps.procurement.services.requisition_revisions.check_requisition_precondition', side_effect=checked), \
                    patch('apps.procurement.services.requisition_concurrency.check_requisition_precondition', side_effect=checked):
                threads[0].start()
                self.assertTrue(acquired.wait(WAIT_SECONDS), f'Winner never reached its locked precondition: {failures}')
                threads[1].start()
                self.assertTrue(prepared['contender'].wait(WAIT_SECONDS), f'Contender could not connect: {failures}')
                self.assertNotEqual(pids['winner'], pids['contender'])
                self.assertEqual(isolations, {'winner': 'read committed', 'contender': 'read committed'})
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    self.assertNotIn(cursor.fetchone()[0], pids.values())
                deadline = monotonic() + WAIT_SECONDS
                blocked = None
                while monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            'SELECT wait_event_type, query, pg_blocking_pids(pid) '
                            'FROM pg_stat_activity WHERE pid = %s', [pids['contender']],
                        )
                        blocked = cursor.fetchone()
                    if blocked and blocked[0] == 'Lock' and pids['winner'] in blocked[2]:
                        break
                    if finished['contender'].wait(0.02):
                        break
                self.assertIsNotNone(blocked)
                self.assertEqual(blocked[0], 'Lock', f'No PostgreSQL lock wait observed: {blocked}; {failures}')
                self.assertIn(pids['winner'], blocked[2])
                # PostgreSQL truncates pg_stat_activity.query according to
                # track_activity_query_size. The PR has enough columns that
                # the trailing FOR UPDATE may be absent from that display.
                # Match the live blocked PID to its exact executing SQL from
                # Django's wrapper, then verify completed locks in the trace.
                self.assertEqual(in_flight['contender'], 'lock')
                self.assertFalse(finished['contender'].is_set())
                release.set()
                for thread in threads:
                    thread.join(WAIT_SECONDS)
                self.assertFalse(any(thread.is_alive() for thread in threads), 'A race worker failed to finish.')
                if failures:
                    raise next(iter(failures.values()))
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(WAIT_SECONDS)
            # Never leave a blocked database worker to contaminate later tests.
            for thread in threads:
                if thread.is_alive() and thread.name in pids:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_terminate_backend(%s)', [pids[thread.name]])
                    thread.join(2)

        # Lock acquisition, comparison and all PR UPDATEs have one real backend
        # transaction ID per request, including nested atomic/savepoint blocks.
        for name, events in trace.items():
            with self.subTest(worker=name):
                kinds = [event['kind'] for event in events]
                self.assertIn('lock', kinds)
                comparison = 'compare' if 'compare' in kinds else 'omitted'
                self.assertIn(comparison, kinds)
                self.assertLess(kinds.index('lock'), kinds.index(comparison))
                self.assertEqual({event['pid'] for event in events}, {pids[name]})
                self.assertEqual(len({event['transaction_id'] for event in events}), 1, events)
                self.assertTrue(all(event['atomic'] and not event['autocommit'] for event in events), events)
                if 'mutate' in kinds:
                    self.assertLess(kinds.index(comparison), kinds.index('mutate'))
        self.assertNotEqual(trace['winner'][0]['transaction_id'], trace['contender'][0]['transaction_id'])
        self.assertIn('mutate', [event['kind'] for event in trace['winner']])
        if results['contender'].status_code == 409:
            self.assertNotIn('mutate', [event['kind'] for event in trace['contender']])
        print('POSTGRESQL_CONCURRENCY_EVIDENCE ' + json.dumps({
            'scenario': self._testMethodName,
            'lock_wait': {'type': blocked[0], 'blocker_pids': blocked[2], 'waiter_pid': pids['contender']},
            'workers': {
                name: {'backend_pid': pids[name], 'transaction_id': events[0]['transaction_id'],
                       'isolation': isolations[name],
                       'events': [event['kind'] for event in events], 'status': results[name].status_code}
                for name, events in trace.items()
            },
        }, sort_keys=True))
        return results['winner'], results['contender']

    def test_same_token_edits_block_then_reject_the_loser(self):
        winner, loser = self.race(
            ('patch', {'notes': 'Winning edit', 'expected_updated_at': self.token}, ''),
            ('patch', {'notes': 'Losing edit', 'expected_updated_at': self.token}, ''),
        )
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assert_conflict(loser)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.notes, 'Winning edit')
        self.assertNotEqual(winner.data['updated_at'], self.token)
        self.assertEqual(self.pr.status, 'draft')
        self.assert_no_new_notifications()

    def test_edit_wins_race_with_submission(self):
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(self.alternate.pk)
        original_route = deepcopy(self.pr.approval_workflow_config)
        winner, loser = self.race(
            ('patch', {'notes': 'Edit committed before submit', 'expected_updated_at': self.token}, ''),
            ('post', {'approval_workflow_config': changed, 'expected_updated_at': self.token}, 'submit/'),
        )
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assert_conflict(loser)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        self.assertEqual(self.pr.notes, 'Edit committed before submit')
        self.assertEqual(self.pr.approval_workflow_config, original_route)
        self.assert_no_new_notifications()

    def test_submission_wins_race_with_edit(self):
        winner, loser = self.race(
            ('post', {'expected_updated_at': self.token}, 'submit/'),
            ('patch', {'notes': 'Must not overwrite submitted content', 'expected_updated_at': self.token}, ''),
        )
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assert_conflict(loser)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.pr.notes, '')
        self.assertEqual(self.notices().get().recipient_id, self.procurement.pk)
        self.tasks['teams'].assert_called_once()

    def test_same_token_submissions_transition_and_notify_once(self):
        winner, loser = self.race(
            ('post', {'expected_updated_at': self.token}, 'submit/'),
            ('post', {'expected_updated_at': self.token}, 'submit/'),
        )
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assert_conflict(loser)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.notices().get().recipient_id, self.procurement.pk)
        self.tasks['teams'].assert_called_once()

    def test_same_token_reopens_archive_one_rejected_round_without_notifying(self):
        submitted = self.request('post', {'expected_updated_at': self.token}, 'submit/')
        self.assertEqual(submitted.status_code, 200, submitted.data)
        approver_client = APIClient()
        approver_client.force_authenticate(self.procurement)
        rejected = self.request('post', {
            'reason': 'Correct the scope before requesting approval again.',
            'expected_updated_at': submitted.data['updated_at'],
        }, 'process_dynamic_rejection/', approver_client)
        self.assertEqual(rejected.status_code, 200, rejected.data)
        self.pr.refresh_from_db()
        rejected_workflow = deepcopy(self.pr.approval_workflow_config)
        self.original_notification_ids = set(Notification.objects.values_list('pk', flat=True))
        for task in self.tasks.values():
            task.reset_mock()

        operation = ('post', {'expected_updated_at': rejected.data['updated_at']}, 'reopen/')
        winner, loser = self.race(operation, operation)
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assert_conflict(loser)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'draft')
        history = self.pr.price_remarks_data['approval_revision_history']
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]['approval_workflow_config'], rejected_workflow)
        self.assertEqual(history[0]['snapshot']['status'], 'rejected')
        self.assertTrue(all(stage['status'] == 'pending' for stage in self.pr.approval_workflow_config))
        self.assertTrue(set(stage['assignment_id'] for stage in self.pr.approval_workflow_config).isdisjoint(
            stage.get('assignment_id') for stage in rejected_workflow
        ))
        self.assert_no_new_notifications()

    def test_stale_edit_and_submit_leave_all_row_fields_and_notifications_unchanged(self):
        updated = self.request('patch', {'notes': 'New saved content', 'expected_updated_at': self.token})
        self.assertEqual(updated.status_code, 200, updated.data)
        changed = deepcopy(self.workflow)
        changed[0]['user_id'] = str(self.alternate.pk)
        before = self.snapshot()
        for method, action in (('patch', ''), ('post', 'submit/')):
            with self.subTest(method=method):
                response = self.request(method, {
                    'expected_updated_at': self.token, 'notes': 'Stale replacement',
                    'approval_workflow_config': changed,
                    'total_price': '250.00', 'net_total_excl_vat': '250.00',
                    'management_approval_remarks': 'Must not persist',
                }, action)
                self.assert_conflict(response)
                self.assertEqual(self.snapshot(), before)
        self.assert_no_new_notifications()

    def test_legacy_tokenless_edits_serialize_but_last_writer_wins(self):
        winner, follower = self.race(
            ('patch', {'notes': 'First legacy edit'}, ''),
            ('patch', {'notes': 'Later legacy edit'}, ''),
        )
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assertEqual(follower.status_code, 200, follower.data)
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.notes, 'Later legacy edit')
        self.assertEqual(self.pr.status, 'draft')
        self.assert_no_new_notifications()

    def test_legacy_tokenless_submissions_are_idempotent_for_active_review(self):
        winner, follower = self.race(('post', {}, 'submit/'), ('post', {}, 'submit/'))
        self.assertEqual(winner.status_code, 200, winner.data)
        self.assertEqual(follower.status_code, 200, follower.data)
        self.assertEqual(winner.data['updated_at'], follower.data['updated_at'])
        self.pr.refresh_from_db()
        self.assertEqual(self.pr.status, 'submitted')
        self.assertEqual(self.notices().get().recipient_id, self.procurement.pk)
        self.tasks['teams'].assert_called_once()

    def test_module_editor_can_edit_but_cannot_submit_another_issuers_draft(self):
        UserRole.objects.create(user_profile=self.engineer.rbac_profile, role=self.editor_role)
        cache.clear()
        self.client.force_authenticate(get_user_model().objects.get(pk=self.engineer.pk))
        edited = self.request('patch', {'notes': 'Authorized module edit', 'expected_updated_at': self.token})
        self.assertEqual(edited.status_code, 200, edited.data)
        before = self.snapshot()
        # Even a now-stale token must not replace the existing issuer denial.
        denied = self.request('post', {'expected_updated_at': self.token}, 'submit/')
        self.assertEqual(denied.status_code, 403, denied.data)
        self.assertEqual(self.snapshot(), before)
        self.assert_no_new_notifications()

    def test_token_does_not_grant_unprivileged_user_edit_or_submit_access(self):
        outsider = get_user_model().objects.create_user('pg-outsider', email='outsider@pg.example.test')
        self.client.force_authenticate(outsider)
        before = self.snapshot()
        for method, action in (('patch', ''), ('post', 'submit/')):
            with self.subTest(method=method):
                denied = self.request(method, {'notes': 'Forbidden', 'expected_updated_at': self.token}, action)
                self.assertEqual(denied.status_code, 403, denied.data)
                self.assertEqual(self.snapshot(), before)
