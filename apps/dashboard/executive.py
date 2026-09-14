"""Read-only executive view of authorized operational registers.

This is not a consolidated financial ledger. Each contributor has its own read
grant and retains its source's row visibility. A failed source never becomes 0.
"""
import logging
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Sum, Value
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed


logger = logging.getLogger(__name__)
SEVERITY_ORDER = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3}
SOURCE_MODULES = {
    'project_control', 'finance_incoming', 'finance_outgoing',
    'sales_opportunities', 'sales_handovers', 'procurement', 'procurement_orders',
    'procurement_requisitions', 'procurement_receipts', 'procurement_vendors',
    'hr_management', 'qhse', 'qhse_detailed', 'qhse_quality', 'process_datasheet',
    'electrical_datasheet',
}
DEPARTMENTS = {
    'engineering': ('Engineering', '/engineering'),
    'finance': ('Finance', '/finance'),
    'hr': ('Human Resources', '/hr/dashboard'),
    'sales': ('Sales', '/sales'),
    'project_control': ('Project Control', '/projects'),
    'procurement': ('Procurement', '/procurement'),
    'qhse': ('QHSE', '/qhse'),
}


def metric(identifier, label, value=None, *, unit='count', status='available',
           description='', source='', route=None, by_currency=None):
    row = {
        'id': identifier, 'label': label, 'value': value, 'unit': unit,
        'status': status, 'description': description, 'definition': description,
        'reason': description if status != 'available' else None,
        'source': source, 'route': route, 'target': None, 'trend': None,
        'period': 'current_snapshot',
    }
    if by_currency is not None:
        row['by_currency'] = by_currency
    return row


def department(identifier, *, status='available', metrics=None, actions=None, limitations=None):
    label, route = DEPARTMENTS[identifier]
    return {'id': identifier, 'label': label, 'route': route, 'status': status,
            'metrics': metrics or [], 'actions': actions or [], 'limitations': limitations or []}


def restricted(identifier):
    return department(identifier, status='restricted', limitations=[
        'A separate read grant for this source register is required.',
    ])


def _source_timestamp(section, timestamps, label):
    """A source record update is distinct from this response's retrieval time."""
    latest = max((value for value in timestamps if value is not None), default=None)
    section.update({
        'source_updated_at': latest.isoformat() if latest else None,
        'source_timestamp_kind': 'latest_record_update' if latest else None,
        'source_timestamp_label': label,
    })
    return section


def action(identifier, department_id, title, *, severity='medium', owner=None,
           route=None, detail='', due_date=None):
    return {'id': identifier, 'department': department_id, 'title': title,
            'severity': severity, 'owner': owner or 'Unassigned',
            'action_label': 'Review', 'route': route or DEPARTMENTS[department_id][1],
            'detail': detail, 'due_date': due_date.isoformat() if hasattr(due_date, 'isoformat') else due_date}


def _money_rows(queryset, field):
    # Never add amounts in unlike currencies, and never substitute a display currency.
    totals = defaultdict(Decimal)
    for row in queryset.values('currency').annotate(amount=Sum(field)).order_by('currency'):
        if row['amount'] is not None:
            currency = (row['currency'] or '').strip().upper() or 'UNSPECIFIED'
            totals[currency] += Decimal(row['amount'])
    return [{'currency': currency, 'amount': str(amount.quantize(Decimal('0.01')))}
            for currency, amount in sorted(totals.items())]


def _money_metric(identifier, label, rows, source, description):
    return metric(identifier, label, unit='currency', by_currency=rows,
                  status='available' if rows else 'unavailable', source=source,
                  description=description if rows else 'No recorded amounts in the accessible register.')


def _visible(queryset, user, module, owner_field):
    # The same row policy used by Finance and Sales TeamCollaborationMixin.
    from apps.rbac.data_visibility_mixin import build_visibility_filter
    return queryset.filter(build_visibility_filter(
        user=user, module_code=module, owner_field=owner_field,
    )).distinct()


def _build_projects(user, context):
    if 'project_control' not in context['allowed_modules']:
        return restricted('project_control'), {'status': 'restricted', 'counts': None, 'projects': []}
    from apps.project_control.access import accessible_enterprise_projects
    from apps.project_control.services.portfolio_exceptions import build_portfolio_exception_dashboard

    projects = accessible_enterprise_projects(user)
    register = {str(row['pk']): row for row in projects.values(
        'pk', 'client_name', 'contract_value', 'currency', 'updated_at',
    )}
    report = build_portfolio_exception_dashboard(projects)
    # Request-local reuse by the portfolio tab; never compute totals from the
    # overview's bounded preview or issue a second full governance read.
    context['_portfolio_exception_report'] = report
    actions, rows = [], []
    for row in report['projects']:
        project = row['project']
        route = '/projects?' + urlencode({'project': str(project['id']), 'view': 'dashboard'})
        snapshot = row['latest_snapshot']
        details = register[str(project['id'])]
        rows.append({
            'id': str(project['id']), 'code': project['code'], 'name': project['name'],
            'status': project['status'], 'progress_pct': project['progress_pct'],
            'owner': project['owner']['name'], 'health': row['overall_severity'],
            'exception_count': row['exception_count'], 'route': route,
            'data_date': snapshot['data_date'].isoformat() if snapshot else None,
            'client_name': details['client_name'],
            'contract_value': str(details['contract_value'].quantize(Decimal('0.01')))
            if details['contract_value'] is not None else None,
            'currency': (details['currency'] or '').strip().upper() or None,
            # No governed project business-unit, schedule-days or final-margin field exists.
            'business_unit': None, 'schedule_variance': None, 'forecast_margin': None,
        })
        for index, issue in enumerate(row['exceptions']):
            actions.append(action(
                f"project-{project['id']}-{issue['code']}-{index}", 'project_control',
                f"{project['code']}: {issue['title']}", severity=issue['severity'],
                owner=issue['owner']['name'],
                route='/projects?' + urlencode({'project': str(project['id']), 'view': issue['target_view']}),
                detail=issue['detail'],
            ))
    summary = report['summary']
    at_risk = summary['critical_projects'] + summary['high_projects']
    counts = {'total': len(rows), 'active': sum(row['status'] == 'active' for row in rows),
              'at_risk': at_risk, 'clear': summary['clear_projects'],
              'with_exceptions': len(rows) - summary['clear_projects'],
              'medium': summary['medium_projects']}
    section = department('project_control', metrics=[
        metric('active_projects', 'Active projects', counts['active'], source='Enterprise project register',
               description='Accessible projects currently marked active.'),
        metric('projects_at_risk', 'Projects requiring attention', at_risk, source='Governed portfolio exceptions',
               description='Projects with high or critical exceptions, including cost, schedule and reporting controls.'),
        metric('sealed_projects', 'Projects with sealed reporting', sum(row['data_date'] is not None for row in rows),
               source='Integrated reporting snapshots', description='Projects with at least one sealed management snapshot.'),
    ], actions=actions, limitations=[
        'Project health includes governance and reporting exceptions; missing reporting is not a healthy project.',
        'Each project shows its own reporting date. No combined currency or period-based financial total is inferred.',
    ])
    _source_timestamp(section, [row['updated_at'] for row in register.values()], 'Latest project register update')
    return section, {'status': 'available', 'counts': counts, 'projects': rows[:20], 'total_rows': len(rows)}


def _build_finance(user, context):
    allowed = context['allowed_modules']
    if not allowed.intersection({'finance_incoming', 'finance_outgoing'}):
        return restricted('finance')
    from apps.finance.models import Invoice
    from apps.invoice_tracker.models import CustomerInvoice

    can_ap, can_ar = 'finance_incoming' in allowed, 'finance_outgoing' in allowed
    ap = _visible(Invoice.objects.all(), user, 'finance', 'submitted_by') if can_ap else Invoice.objects.none()
    ar = CustomerInvoice.objects.all() if can_ar else CustomerInvoice.objects.none()
    active_ap = ap.exclude(payment_status='cancelled')
    active_ar = ar.exclude(payment_status='cancelled')
    money_field = DecimalField(max_digits=20, decimal_places=2)
    zero = Value(Decimal('0'), output_field=money_field)
    payable_balance = ExpressionWrapper(Greatest(
        F('total_amount') - Coalesce(F('paid_amount'), zero), zero,
    ), output_field=money_field)
    metrics, actions = [], []
    for can_read, identifier, label, queryset, amount, source, required_field in [
        (can_ar, 'receivables', 'Outstanding receivables', active_ar, 'balance_to_be_received',
         'Customer invoice register', 'balance_to_be_received'),
        (can_ap, 'payables', 'Outstanding payables', active_ap, payable_balance,
         'Supplier invoice register', 'total_amount'),
    ]:
        if can_read:
            missing = queryset.filter(**{f'{required_field}__isnull': True}).count()
            if missing:
                metrics.append(metric(identifier, label, unit='currency', status='unavailable', source=source,
                                      description=f'{missing} invoice records lack a required balance amount; the total is withheld.'))
            else:
                metrics.append(_money_metric(identifier, label, _money_rows(queryset, amount), source,
                                             'Recorded outstanding invoice balances, kept separate by currency.'))
        else:
            metrics.append(metric(identifier, label, unit='currency', status='restricted',
                                  description='The underlying invoice register requires a separate read grant.'))
    if can_ar:
        overdue = ar.exclude(payment_status__in=['paid', 'cancelled']).filter(
            due_date__lt=timezone.localtime(context['generated_at']).date(), balance_to_be_received__gt=0,
        )
        count = overdue.count()
        metrics.append(metric('overdue_receivables', 'Overdue receivables', count,
                              source='Customer invoice register', description='Unpaid invoices past their due date with a recorded positive balance.'))
        if count:
            actions.append(action('finance-overdue-receivables', 'finance', f'{count} overdue customer invoices',
                                  severity='high', owner='Finance', route='/finance/outgoing-invoices',
                                  detail='Review collection owners and due dates in the customer invoice register.'))
    if can_ap:
        count = ap.filter(match_status='exception').count()
        metrics.append(metric('invoice_match_exceptions', 'Invoice match exceptions', count,
                              source='Supplier invoice register', description='Recorded three-way matching exceptions.'))
        if count:
            actions.append(action('finance-invoice-matching', 'finance', f'{count} supplier invoice match exceptions',
                                  severity='high', owner='Finance', route='/finance/incoming-invoices',
                                  detail='Review the underlying invoice and purchase order before settlement.'))
    section = department('finance', metrics=metrics, actions=actions, limitations=[
        'Invoice balances represent working-capital exposure, not revenue, EBITA or bank cash.',
        'No FX conversion is applied. Source registers do not establish consolidated group coverage.',
    ])
    timestamps = [qs.aggregate(latest=Max('updated_at'))['latest'] for qs, permitted in [(ap, can_ap), (ar, can_ar)] if permitted]
    return _source_timestamp(section, timestamps, 'Latest authorized invoice register update')


def _pipeline_stages(active):
    """Count active CRM opportunities and group money without currency conversion."""
    from apps.sales.models import DEAL_STAGES
    grouped = {}
    values = active.values('stage', 'currency').annotate(
        count=Count('pk'), amount=Sum('estimated_value'), weighted_amount=Sum('weighted_value'),
    ).order_by('stage', 'currency')
    for row in values:
        stage = grouped.setdefault(row['stage'], {'count': 0, 'currencies': {}})
        stage['count'] += row['count']
        currency = (row['currency'] or '').strip().upper() or 'UNSPECIFIED'
        money = stage['currencies'].setdefault(currency, {'amount': Decimal('0'), 'weighted_amount': Decimal('0')})
        money['amount'] += row['amount']
        money['weighted_amount'] += row['weighted_amount']
    return [{
        'stage': code, 'label': DEAL_STAGES[code]['name'], 'count': grouped[code]['count'],
        'by_currency': [{'currency': currency, **{key: str(value.quantize(Decimal('0.01'))) for key, value in amounts.items()}}
                        for currency, amounts in sorted(grouped[code]['currencies'].items())],
    } for code in ['lead', 'qualified', 'proposal', 'negotiation', 'award_pending'] if code in grouped]


def _build_sales(user, context):
    if 'sales_opportunities' not in context['allowed_modules']:
        return restricted('sales')
    from apps.sales.models import Deal
    deals = _visible(Deal.objects.all(), user, 'sales', 'owner')
    active = deals.filter(stage__in=['lead', 'qualified', 'proposal', 'negotiation', 'award_pending'])
    today = timezone.localtime(context['generated_at']).date()
    due = active.filter(submission_due_date__lte=today + timedelta(days=7))
    awards = active.filter(stage='award_pending')
    actions = []
    for deal in due.select_related('owner').order_by('submission_due_date', 'id')[:15]:
        actions.append(action(
            f'sales-tender-{deal.pk}', 'sales', f'{deal.deal_code}: proposal deadline',
            severity='high' if deal.submission_due_date < today else 'medium',
            owner=deal.owner.get_full_name() if deal.owner else None,
            route='/sales/opportunities', due_date=deal.submission_due_date,
            detail='Review submission readiness and the opportunity owner.',
        ))
    if awards.exists():
        actions.append(action('sales-award-approvals', 'sales', f'{awards.count()} awards awaiting approval',
                              severity='high', owner='Sales', route='/sales/opportunities',
                              detail='Review the governed award approval before project conversion.'))
    result = department('sales', metrics=[
        metric('active_opportunities', 'Active opportunities', active.count(), source='CRM opportunity register',
               description='Opportunities in lead, qualification, proposal, negotiation or award approval.'),
        _money_metric('weighted_pipeline', 'Weighted pipeline', _money_rows(active, 'weighted_value'),
                      'CRM opportunity register', 'Stored opportunity value weighted by stage probability; currencies remain separate.'),
        metric('proposals_due', 'Proposals due or overdue', due.count(), source='CRM submission deadlines',
               description='Active opportunity submission dates overdue or due within seven days.'),
    ], actions=actions, limitations=[
        'Pipeline is based on CRM records and stage probabilities; it is not recognized revenue or signed backlog.',
        'The source register is shared under existing Sales visibility rules; group consolidation is not established.',
    ])
    result['action_count'] = due.count() + (1 if awards.exists() else 0)
    result['pipeline_stages'] = _pipeline_stages(active)
    return _source_timestamp(result, [deals.aggregate(latest=Max('updated_at'))['latest']], 'Latest authorized CRM opportunity update')


def _build_procurement(user, context):
    allowed = context['allowed_modules']
    if not allowed.intersection({'procurement_orders', 'procurement_requisitions'}):
        return restricted('procurement')
    from apps.procurement.models import PurchaseOrder, PurchaseRequisition
    from apps.project_control.access import accessible_enterprise_projects

    projects = accessible_enterprise_projects(user)
    metrics, actions, action_count, source_updates = [], [], 0, []
    today = timezone.localtime(context['generated_at']).date()
    if 'procurement_orders' in allowed:
        orders = PurchaseOrder.objects.filter(enterprise_project__in=projects)
        source_updates.append(orders.aggregate(latest=Max('updated_at'))['latest'])
        commitments = orders.exclude(status__in=['draft', 'cancelled'])
        overdue = commitments.exclude(status='completed').filter(expected_delivery__lt=today)
        action_count = overdue.count()
        metrics.extend([
            _money_metric('po_commitments', 'Issued purchase order value', _money_rows(commitments, 'total_amount'),
                          'Project-linked purchase orders', 'Issued purchase orders, excluding drafts and cancellations; currencies remain separate.'),
            metric('overdue_deliveries', 'Overdue deliveries', overdue.count(), source='Project-linked purchase orders',
                   description='Non-completed commitments whose expected delivery date has passed.'),
        ])
        for order in overdue.order_by('expected_delivery', 'pk')[:15]:
            actions.append(action(f'procurement-delivery-{order.pk}', 'procurement',
                                  f'{order.po_number}: overdue delivery', severity='high', owner='Procurement',
                                  route=f'/procurement/orders/{order.pk}', due_date=order.expected_delivery,
                                  detail='Review supplier delivery confirmation and the project impact.'))
    if 'procurement_requisitions' in allowed:
        pending = PurchaseRequisition.objects.filter(
            enterprise_project__in=projects, status__in=['submitted', 'in_review'],
        )
        source_updates.append(pending.aggregate(latest=Max('updated_at'))['latest'])
        metrics.append(metric('pending_requisitions', 'Requisitions awaiting review', pending.count(),
                              source='Project-linked requisitions', description='Submitted or in-review requisitions for accessible projects.'))
    result = department('procurement', metrics=metrics, actions=actions, limitations=[
        'Only records linked to accessible enterprise projects are included; unlinked procurement records are excluded.',
        'Received monetary value and realized savings require approved source data and are not inferred.',
    ])
    result['action_count'] = action_count
    return _source_timestamp(result, source_updates, 'Latest update among included procurement records')


def build_executive_dashboard(user):
    now = timezone.now()
    context = {'generated_at': now, 'is_admin': bool(user.is_superuser),
               'allowed_modules': {code for code in SOURCE_MODULES if module_action_allowed(user, code, 'read')}}
    sections = {}
    portfolio = {'status': 'unavailable', 'counts': None, 'projects': []}
    for identifier, builder in [('project_control', _build_projects), ('finance', _build_finance),
                                ('sales', _build_sales), ('procurement', _build_procurement)]:
        try:
            # A missing optional source table must not poison the other contributors.
            with transaction.atomic():
                result = builder(user, context)
            if identifier == 'project_control':
                sections[identifier], portfolio = result
            else:
                sections[identifier] = result
        except Exception:
            logger.exception('Executive dashboard source unavailable: %s', identifier)
            sections[identifier] = department(identifier, status='error', limitations=[
                'The source could not be read. Refresh or ask the source owner to investigate.',
            ])
            if identifier == 'project_control':
                portfolio = {'status': 'error', 'counts': None, 'projects': []}
    from .executive_people import build_people_departments
    for section in build_people_departments(user, context):
        sections[section['id']] = section
    departments = [sections[identifier] for identifier in DEPARTMENTS]
    for section in departments:
        section.setdefault('route', DEPARTMENTS[section['id']][1])
        section.setdefault('source_updated_at', None)
        section.setdefault('source_timestamp_kind', None)
        section.setdefault('source_timestamp_label', 'Source record update unavailable')
        if section['id'] == 'qhse':
            overdue = next((row for row in section['metrics'] if row['id'] == 'overdue_car_projects'), None)
            if overdue and overdue['status'] == 'available':
                section['action_count'] = overdue['value']
        section.setdefault('action_count', len(section.get('actions', [])))
        section['actions_returned'] = len(section.get('actions', []))
        section['actions_truncated'] = section['action_count'] > section['actions_returned']
        if section['actions_truncated']:
            section['limitations'].append('The action preview is limited; open the source register for all matching records.')
        for row in section['metrics']:
            row.setdefault('definition', row.get('description', ''))
            row.setdefault('reason', row.get('description') if row['status'] != 'available' else None)
            row.setdefault('target', None)
            row.setdefault('trend', None)
            row.setdefault('period', 'current_snapshot')
        for row in section.get('actions', []):
            row.setdefault('due_date', None)
    headline_definitions = [
        ('revenue', 'Revenue', 'currency', 'Recognized revenue requires an authoritative accounting source.'),
        ('ebita_margin', 'EBITA margin', 'percent', 'EBITA and net sales require approved financial reporting.'),
        ('operating_cash_flow', 'Operating cash flow', 'currency', 'An approved cash-flow statement is not connected.'),
        ('signed_backlog', 'Signed backlog', 'currency', 'Remaining signed contract value requires a controlled backlog register.'),
        ('utilisation', 'Billable utilisation', 'percent', 'Approved billable hours and available capacity need a common reporting period.'),
    ]
    kpis = [metric(key, label, unit=unit, status='unavailable', description=reason,
                   source='Authoritative source not connected') for key, label, unit, reason in headline_definitions]
    kpis.append(metric('projects_at_risk', 'Projects at risk',
                       portfolio['counts']['at_risk'] if portfolio['counts'] is not None else None,
                       status=portfolio['status'], source='Governed portfolio exceptions', route='/projects',
                       description='Projects with high or critical cost, schedule, governance or reporting exceptions.'))
    all_actions = [row for section in departments for row in section.get('actions', [])]
    all_actions.sort(key=lambda row: (SEVERITY_ORDER.get(row['severity'], 9), row.get('due_date') or '9999', row['id']))
    hr = sections['hr']
    workforce = {
        'status': hr['status'], 'unit': 'people',
        'allocation': [{'label': row['department'], 'count': row['headcount']} for row in hr.get('workforce_by_department', [])],
        'source': 'Employee master', 'description': 'Current employees by recorded department; not FTE.',
        'critical_roles': None, 'capacity_gap': None,
        'source_updated_at': hr['source_updated_at'], 'source_timestamp_kind': hr['source_timestamp_kind'],
    }
    from .financial_performance import build_financial_performance
    financial_performance = build_financial_performance(user, context, sections['finance'])
    from .portfolio_performance import build_portfolio_performance
    portfolio_performance = build_portfolio_performance(user, context, sections['project_control'])
    from .commercial_performance import build_commercial_performance
    commercial_performance = build_commercial_performance(user, context)
    from .workforce_performance import build_workforce_performance
    workforce_performance = build_workforce_performance(user, context)
    from .risk_compliance import build_risk_compliance
    risk_compliance = build_risk_compliance(user, context)
    return {
        'schema_version': '1.0', 'generated_at': now.isoformat(),
        'scope': {'label': 'Authorized RADAI workspace', 'type': 'authorized_workspace',
                  'period': 'current_snapshot', 'consolidated': False},
        'kpis': kpis, 'departments': departments, 'actions': all_actions[:50],
        'action_count': sum(section['action_count'] for section in departments),
        'actions_returned': min(len(all_actions), 50),
        'actions_truncated': sum(section['action_count'] for section in departments) > min(len(all_actions), 50),
        'portfolio': portfolio,
        'workforce': workforce,
        'financial_performance': financial_performance,
        'portfolio_performance': portfolio_performance,
        'commercial_performance': commercial_performance,
        'workforce_performance': workforce_performance,
        'risk_compliance': risk_compliance,
        'coverage': {'available_departments': sum(row['status'] == 'available' for row in departments),
                     'total_departments': len(DEPARTMENTS)},
        'limitations': [
            'Current operational snapshot of authorized source registers; not a Rejlers AB group consolidation.',
            'A generated timestamp records this read, not the freshness of every underlying record.',
            'Missing, restricted and failed sources are shown explicitly. No synthetic financial values or trends are used.',
        ],
    }
