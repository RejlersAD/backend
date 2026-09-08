"""Create the governed Project Control demonstration for one existing project.

This command is deliberately local/development only.  It links an existing
Planning Intelligence workspace and adds stable ``DEMO-*`` control records;
it never creates fake uploaded files and never deletes an existing record.
"""
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.core.project_models import Project
from apps.finance.models import Invoice, InvoiceMatchStatus, InvoicePurchaseOrderAllocation
from apps.planning_intelligence.models import PlanningProject
from apps.procurement.models import PurchaseOrder, Vendor
from apps.project_control.models import (
    ApprovedHourEntry,
    BudgetAllocation,
    ControlAccount,
    CostAllocation,
    IntegratedReportingSnapshot,
    ReportingPeriod,
    ReportingPeriodAudit,
    WBSNode,
)
from apps.project_control.services.actuals import create_integrated_snapshot, reconcile_reporting_period


DEMO_MARKER = 'Local lifecycle demonstration for enterprise project 5900913.'

CONTROL_STRUCTURE = (
    ('1.0', 'Project Management', 'CA-PM', 'Project Management', 'level_of_effort', Decimal('360000')),
    ('2.0', 'Process Engineering', 'CA-PRO', 'Process Engineering', 'percent_complete', Decimal('720000')),
    ('3.0', 'Multidiscipline Engineering', 'CA-ENG', 'Multidiscipline Engineering', 'weighted_milestone', Decimal('960000')),
    ('4.0', 'Procurement Support', 'CA-PROC', 'Procurement Support', 'units_complete', Decimal('360000')),
)

HOUR_ENTRIES = (
    ('CA-PM', 'DEMO-5900913-202608-PM', 'DEMO-PM-01', 'Project controls team', date(2026, 8, 14), Decimal('320'), Decimal('450')),
    ('CA-PRO', 'DEMO-5900913-202608-PRO', 'DEMO-PRO-01', 'Process engineering team', date(2026, 8, 21), Decimal('640'), Decimal('375')),
    ('CA-ENG', 'DEMO-5900913-202608-ENG', 'DEMO-ENG-01', 'Engineering delivery team', date(2026, 8, 28), Decimal('520'), Decimal('325')),
)


class Command(BaseCommand):
    help = 'DEV-ONLY: configure project 5900913 as the end-to-end Project Control demonstration.'

    def add_arguments(self, parser):
        parser.add_argument('--project-code', default='5900913')
        parser.add_argument('--workspace-id', type=int, default=14)

    def handle(self, *args, **options):
        environment = str(getattr(settings, 'ENVIRONMENT', '')).lower()
        if environment == 'production' or not getattr(settings, 'DEBUG', False):
            raise CommandError('REFUSED: the lifecycle demonstration can run only with DEBUG=True outside production.')

        try:
            project = Project.objects.select_related('owner').get(
                code=options['project_code'], is_deleted=False,
            )
        except Project.DoesNotExist as exc:
            raise CommandError(f"Enterprise project {options['project_code']} was not found.") from exc
        try:
            workspace = PlanningProject.objects.get(pk=options['workspace_id'], is_deleted=False)
        except PlanningProject.DoesNotExist as exc:
            raise CommandError(f"Planning workspace {options['workspace_id']} was not found.") from exc

        actor = project.owner or get_user_model().objects.filter(is_superuser=True, is_active=True).first()
        if actor is None:
            raise CommandError('The project needs an owner (or the system needs an active superuser).')

        with transaction.atomic():
            linkage_note = self._link_workspace(project, workspace)
            accounts = self._control_structure(project, actor)
            august = self._closed_august_period(project, actor, accounts)
            september, _ = ReportingPeriod.objects.get_or_create(
                project=project,
                sequence=2,
                defaults={
                    'name': 'September 2026', 'start_date': date(2026, 9, 1),
                    'end_date': date(2026, 9, 30), 'data_date': date(2026, 9, 8),
                    'status': 'open', 'notes': DEMO_MARKER, 'created_by': actor,
                },
            )
            if september.status not in {'open', 'reopened'}:
                raise CommandError('September 2026 already exists but is not an open entry window.')
            ReportingPeriodAudit.objects.get_or_create(
                period=september, action='created', to_status='open',
                defaults={'actor': actor, 'reason': DEMO_MARKER},
            )

        schedule = workspace.schedules.filter(is_deleted=False).first()
        version = schedule.versions.filter(is_deleted=False).order_by('-version').first() if schedule else None
        baseline_ready = bool(version and version.status in {'approved', 'baselined'})
        blocker_count = 0
        if version:
            review = version.assurance_reviews.filter(is_deleted=False).order_by('-created_at').first()
            blocker_count = len(review.blockers or []) if review else 0

        snapshot = IntegratedReportingSnapshot.objects.get(reporting_period=august)
        self.stdout.write(self.style.SUCCESS(f'Project lifecycle demonstration ready for {project.code}.'))
        self.stdout.write(f'  Planning workspace: {workspace.id} - {workspace.name} ({linkage_note})')
        self.stdout.write(f'  Planning evidence: {workspace.files.filter(is_deleted=False).count()} files; '
                          f'{version.activities.filter(is_deleted=False).count() if version else 0} activities')
        self.stdout.write(f'  Schedule governance: {version.status if version else "missing"}; '
                          f'{blocker_count} assurance blocker group(s); baseline ready={baseline_ready}')
        self.stdout.write(f'  Project controls: {len(accounts)} active Control Accounts; AED '
                          f'{snapshot.budget_at_completion:,.2f} approved control budget')
        self.stdout.write(f'  August close: locked; AC AED {snapshot.actual_cost:,.2f}; '
                          f'CPI {snapshot.cpi}; SPI {snapshot.spi}; immutable snapshot v{snapshot.version}')
        self.stdout.write(f'  Current entry window: {september.name} ({september.status})')
        if not baseline_ready:
            self.stdout.write(self.style.WARNING(
                '  Exact planning issue: the calculated schedule is not approved/baselined. '
                'Resolve its assurance blockers before it can become the governed schedule baseline.'
            ))

    def _link_workspace(self, project, workspace):
        if workspace.enterprise_project_id not in {None, project.pk}:
            raise CommandError(
                f'Workspace {workspace.pk} is already linked to enterprise project '
                f'{workspace.enterprise_project_id}.'
            )
        current = PlanningProject.objects.filter(
            enterprise_project=project, is_deleted=False,
        ).exclude(pk=workspace.pk).first()
        if current:
            has_content = (
                current.files.filter(is_deleted=False).exists()
                or current.generations.filter(is_deleted=False).exists()
                or current.schedules.filter(is_deleted=False).exists()
            )
            if has_content:
                raise CommandError(
                    f'Project {project.code} is linked to non-empty workspace {current.pk}; '
                    'no automatic relinking was performed.'
                )
            current.enterprise_project = None
            current.save(update_fields=['enterprise_project', 'updated_at'])
        workspace.enterprise_project = project
        workspace.save(update_fields=['enterprise_project', 'updated_at'])
        return 'existing evidence linked; empty duplicate preserved unlinked' if current else 'already linked'

    def _control_structure(self, project, actor):
        now = timezone.now()
        accounts = {}
        for sort_order, (wbs_code, wbs_name, account_code, account_name, ev_method, amount) in enumerate(
            CONTROL_STRUCTURE, start=1,
        ):
            wbs, _ = WBSNode.objects.update_or_create(
                project=project, code=wbs_code,
                defaults={'name': wbs_name, 'level': 1, 'sort_order': sort_order, 'is_deleted': False},
            )
            BudgetAllocation.objects.update_or_create(
                project=project, code=f'BUD-{account_code[3:]}',
                defaults={
                    'wbs_node': wbs, 'name': account_name, 'category': wbs_name,
                    'amount': amount, 'currency': project.currency or 'AED', 'status': 'approved',
                    'notes': DEMO_MARKER, 'approved_by': actor, 'approved_at': now, 'is_deleted': False,
                },
            )
            account, _ = ControlAccount.objects.update_or_create(
                project=project, code=account_code,
                defaults={
                    'wbs_node': wbs, 'name': account_name, 'manager': actor,
                    'earned_value_method': ev_method,
                    'baseline_start': project.start_date or date(2026, 1, 25),
                    'baseline_finish': project.end_date or date(2026, 10, 9),
                    'status': 'active', 'notes': DEMO_MARKER, 'created_by': actor,
                    'submitted_by': actor, 'submitted_at': now,
                    'approved_by': actor, 'approved_at': now, 'is_deleted': False,
                },
            )
            accounts[account_code] = account
        return accounts

    def _closed_august_period(self, project, actor, accounts):
        existing_open = ReportingPeriod.objects.filter(
            project=project, status__in=['open', 'reopened'], is_deleted=False,
        ).exclude(sequence=1).first()
        august, created = ReportingPeriod.objects.get_or_create(
            project=project,
            sequence=1,
            defaults={
                'name': 'August 2026', 'start_date': date(2026, 8, 1),
                'end_date': date(2026, 8, 31), 'data_date': date(2026, 8, 31),
                'status': 'open', 'notes': DEMO_MARKER, 'created_by': actor,
            },
        )
        if created:
            ReportingPeriodAudit.objects.create(
                period=august, actor=actor, action='created', to_status='open', reason=DEMO_MARKER,
            )
        if august.status == 'locked':
            if not IntegratedReportingSnapshot.objects.filter(reporting_period=august).exists():
                raise CommandError('August is locked but has no immutable integrated snapshot.')
            return august
        if august.status not in {'open', 'reopened'}:
            raise CommandError(f'August exists in {august.status} state; complete it through the UI.')
        if existing_open:
            raise CommandError(
                f'{existing_open.name} is already open. Submit it before preparing the August demonstration close.'
            )

        self._hours(project, august, actor, accounts)
        self._finance_actual(project, actor, accounts['CA-PROC'])
        reconciliation = reconcile_reporting_period(august, user=actor)
        if reconciliation.exception_count:
            raise CommandError(f'Reconciliation produced exceptions: {reconciliation.exceptions}')

        now = timezone.now()
        previous = august.status
        august.status = 'submitted'
        august.submitted_by = actor
        august.submitted_at = now
        august.save(update_fields=['status', 'submitted_by', 'submitted_at', 'updated_at'])
        ReportingPeriodAudit.objects.create(
            period=august, actor=actor, action='submitted', from_status=previous,
            to_status='submitted', reason=DEMO_MARKER,
        )
        create_integrated_snapshot(august, user=actor)
        august.status = 'locked'
        august.locked_by = actor
        august.locked_at = now
        august.save(update_fields=['status', 'locked_by', 'locked_at', 'updated_at'])
        ReportingPeriodAudit.objects.create(
            period=august, actor=actor, action='locked', from_status='submitted',
            to_status='locked', reason=DEMO_MARKER,
        )
        return august

    def _hours(self, project, period, actor, accounts):
        now = timezone.now()
        for account_code, source_ref, employee_code, employee_name, work_date, hours, rate in HOUR_ENTRIES:
            ApprovedHourEntry.objects.update_or_create(
                project=project, source_type='demo_timesheet', source_reference=source_ref,
                defaults={
                    'control_account': accounts[account_code], 'reporting_period': period,
                    'employee_code': employee_code, 'employee_name': employee_name,
                    'work_date': work_date, 'hours': hours, 'hourly_cost_rate': rate,
                    'labor_actual_cost': hours * rate, 'currency': project.currency or 'AED',
                    'status': 'approved', 'notes': DEMO_MARKER, 'created_by': actor,
                    'submitted_by': actor, 'submitted_at': now,
                    'approved_by': actor, 'approved_at': now, 'is_deleted': False,
                },
            )

    def _finance_actual(self, project, actor, account):
        vendor, _ = Vendor.objects.get_or_create(
            vendor_code='DEMO-5900913-VENDOR',
            defaults={'name': 'Demo Engineering Services Vendor', 'status': 'active'},
        )
        order, created = PurchaseOrder.objects.get_or_create(
            po_number='DEMO-5900913-PO-001',
            defaults={
                'vendor': vendor, 'title': 'Specialist engineering services', 'category': 'services',
                'total_amount': Decimal('250000'), 'currency': project.currency or 'AED',
                'status': 'sent', 'enterprise_project': project, 'project_number': project.code,
                'description': DEMO_MARKER,
            },
        )
        if not created and order.enterprise_project_id != project.pk:
            raise CommandError(f'PO {order.po_number} already belongs to another enterprise project.')
        if created:
            PurchaseOrder.objects.filter(pk=order.pk).update(po_date=date(2026, 8, 5))
            order.refresh_from_db()

        budget = BudgetAllocation.objects.get(project=project, code='BUD-PROC')
        CostAllocation.objects.update_or_create(
            project=project, wbs_node=account.wbs_node,
            source_type='purchase_order', source_id=str(order.pk),
            defaults={
                'budget_allocation': budget, 'source_reference': order.po_number,
                'amount': Decimal('250000'), 'currency': project.currency or 'AED',
                'status': 'approved', 'notes': DEMO_MARKER, 'allocated_by': actor,
                'approved_by': actor, 'approved_at': timezone.now(), 'is_deleted': False,
            },
        )
        invoice, _ = Invoice.objects.update_or_create(
            invoice_number='DEMO-5900913-INV-001', vendor=vendor,
            defaults={
                'vendor_name': vendor.name, 'invoice_date': date(2026, 8, 25),
                'amount': Decimal('250000'), 'total_amount': Decimal('250000'),
                'currency': project.currency or 'AED', 'original_filename': 'DEMO-NO-FILE.pdf',
                'file_path': 'demo/no-uploaded-file', 'submitted_by': actor,
            },
        )
        InvoicePurchaseOrderAllocation.objects.update_or_create(
            invoice=invoice, purchase_order=order,
            defaults={
                'allocated_amount': Decimal('250000'), 'currency': project.currency or 'AED',
                'match_status': InvoiceMatchStatus.VERIFIED, 'match_confidence': Decimal('100'),
                'po_amount_at_match': Decimal('250000'), 'invoice_amount_at_match': Decimal('250000'),
                'amount_variance': Decimal('0'), 'amount_within_tolerance': True,
                'vendor_matched': True, 'currency_matched': True, 'receipt_required': False,
                'exception_codes': [], 'review_notes': DEMO_MARKER,
                'matched_by': actor, 'matched_at': timezone.now(),
                'verified_by': actor, 'verified_at': timezone.now(),
            },
        )
