from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied

from apps.sales.analysis_models import OpportunityAnalysis as Report
from apps.sales.models import Client, Deal
from apps.sales.opportunity_analysis import request_analysis, run_analysis_step, due_analysis_ids
from apps.sales.tests.access_fixtures import grant_sales_actions


class AnalysisDispatchTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(username='queue-owner')
        grant_sales_actions(self.actor, 'sales', 'sales_opportunities', 'sales_clients')
        client = Client.objects.create(client_code='QUEUE', company_name='Synthetic Energy', account_manager=self.actor)
        self.deal = Deal.objects.create(deal_code='Q-QUEUE', deal_name='Synthetic inspection',
                                       client=client, owner=self.actor, stage='qualified')
        self.inventory = patch('apps.sales.opportunity_analysis.inventory', return_value=[])
        self.inventory.start()
        self.addCleanup(self.inventory.stop)
        self.snapshot = patch('apps.sales.opportunity_analysis.snapshot', return_value='stable')
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)

    def test_request_dispatches_after_commit_and_returns_numeric_counts(self):
        with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                result = request_analysis(self.deal, self.actor)
                send.assert_not_called()
            report = Report.objects.get(opportunity=self.deal)
            send.assert_called_once_with(args=[str(report.pk)], retry=False)
        self.assertEqual(result['status'], 'queued')
        self.assertEqual(result['counts']['ready'], 0)
        self.assertEqual(result['counts']['partial'], 0)

    def test_broker_failure_preserves_recoverable_intent(self):
        with patch('apps.sales.tasks.analyze_opportunity_step.apply_async', side_effect=OSError('unavailable')):
            with self.captureOnCommitCallbacks(execute=True):
                request_analysis(self.deal, self.actor)
        report = Report.objects.get(opportunity=self.deal)
        self.assertEqual(report.status, 'queued')
        self.assertIn(report.pk, due_analysis_ids())

    def test_refresh_requeues_stranded_job_but_not_active_lease(self):
        report = Report.objects.create(opportunity=self.deal, requested_by=self.actor,
                                       dirty=False, snapshot='stable', status='queued')
        with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                request_analysis(self.deal, self.actor)
            send.assert_called_once()
            send.reset_mock()
            report.lease_until = timezone.now() + timedelta(minutes=2)
            report.save()
            with self.captureOnCommitCallbacks(execute=True):
                request_analysis(self.deal, self.actor)
            send.assert_not_called()

    def test_retry_backoff_is_respected(self):
        Report.objects.create(opportunity=self.deal, requested_by=self.actor, dirty=False,
                              snapshot='stable', next_attempt_at=timezone.now() + timedelta(minutes=1))
        with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                request_analysis(self.deal, self.actor)
            send.assert_not_called()

    def test_worker_finishes_requested_job_without_periodic_scheduler(self):
        with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
            with self.captureOnCommitCallbacks(execute=True):
                request_analysis(self.deal, self.actor)
        report_id = send.call_args.kwargs['args'][0]
        self.assertTrue(run_analysis_step(report_id))
        report = Report.objects.get(pk=report_id)
        self.assertEqual(report.status, 'ready')
        self.assertFalse(report.dirty)
        self.assertIsNone(report.lease_until)

    def test_denied_request_cannot_create_or_dispatch_job(self):
        with patch('apps.sales.opportunity_analysis.authority', side_effect=PermissionDenied):
            with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
                with self.assertRaises(PermissionDenied):
                    request_analysis(self.deal, self.actor)
                send.assert_not_called()
        self.assertFalse(Report.objects.exists())

    def test_three_sources_complete_through_worker_chain_without_beat(self):
        from apps.sales.tasks import analyze_opportunity_step
        sources = [{'key': f'file-{i}', 'identity': f'identity-{i}',
                    'metadata': {'name': f'Synthetic-{i}.txt', 'provider': 'radai'}} for i in range(3)]
        with patch('apps.sales.opportunity_analysis.inventory', return_value=sources):
            with patch('apps.sales.opportunity_analysis._extract_source', side_effect=lambda source, *_:
                       ([('Page 1', f"Required scope: inspection services for {source.source_key}.")], '')):
                with patch('apps.sales.tasks.analyze_opportunity_step.apply_async') as send:
                    with self.captureOnCommitCallbacks(execute=True):
                        request_analysis(self.deal, self.actor)
                    report_id = send.call_args.kwargs['args'][0]
                    for _ in range(10):
                        send.reset_mock()
                        self.assertTrue(analyze_opportunity_step(report_id))
                        report = Report.objects.get(pk=report_id)
                        if report.status == 'ready':
                            send.assert_not_called()
                            break
                        send.assert_called_once()
                    self.assertEqual(report.status, 'ready')
                    self.assertEqual(report.sources.filter(status='ready').count(), 3)
                    self.assertIn('manager_review', report.result)
