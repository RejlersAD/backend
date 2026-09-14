"""Authorized open-project register, governed exceptions and recorded milestones.

Contract values are not remaining revenue. Register progress and exception
severity do not establish delivery confidence, forecast margin or capacity.
"""
import logging
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from .executive import SEVERITY_ORDER, action, metric


logger = logging.getLogger(__name__)
OPEN_STATUSES = ('planning', 'active', 'on_hold')
SCOPE = {'label': 'Accessible open projects: planning, active and on hold',
         'project_statuses': list(OPEN_STATUSES)}
HEALTH_KEYS = ('critical', 'high', 'medium', 'low', 'clear', 'unknown')
GAPS = (
    ('forecast_margin', 'Forecast margin', 'percent', 'Approved final revenue and final cost forecasts on a common basis are not connected.'),
    ('schedule_confidence', 'Schedule confidence', 'percent', 'An approved delivery-confidence assessment is not connected; exception severity and SPI are not probabilities.'),
    ('revenue_remaining', 'Revenue remaining', 'currency', 'Remaining revenue requires approved recognized revenue and contract changes; recorded contract value is not remaining revenue.'),
)


def _route(project_id, view='dashboard'):
    return '/projects?' + urlencode({'project': str(project_id), 'view': view})


def _decimal(value):
    return str(value.quantize(Decimal('0.01')))


def _currency(project):
    return (project.currency or '').strip().upper() or 'UNSPECIFIED'


def _owner(project):
    if not project.owner:
        return 'Unassigned'
    return project.owner.get_full_name() or project.owner.email or project.owner.username


def _contracts(projects):
    """Withhold incomplete currency groups instead of silently summing known rows."""
    grouped = {}
    for project in projects:
        code = _currency(project)
        group = grouped.setdefault(code, {'projects': [], 'missing_contract_count': 0})
        group['projects'].append(project)
        group['missing_contract_count'] += project.contract_value is None
    rows = []
    for code, group in sorted(grouped.items()):
        complete = not group['missing_contract_count'] and code != 'UNSPECIFIED'
        rows.append({
            'currency': code, 'status': 'available' if complete else 'incomplete',
            'total': _decimal(sum((p.contract_value for p in group['projects']), Decimal('0'))) if complete else None,
            'missing_contract_count': group['missing_contract_count'],
            'project_count': len(group['projects']),
        })
    return rows


def _contract_metric(identifier, label, projects, description, *, status='available'):
    groups = _contracts(projects) if status == 'available' else []
    missing = [row['currency'] for row in groups if row['status'] != 'available']
    result = metric(identifier, label, unit='currency',
                    status='partial' if missing else status,
                    by_currency=[{'currency': row['currency'], 'amount': row['total']}
                                 for row in groups if row['status'] == 'available'],
                    source='Enterprise project register', route=None if status == 'restricted' else '/projects',
                    description=description)
    result['incomplete_currencies'] = missing
    result['missing_contract_count'] = sum(row['missing_contract_count'] for row in groups) if status == 'available' else None
    if missing:
        result['reason'] = 'Currency totals are withheld where a contract value or currency is missing.'
    return result


def _unavailable(identifier, label, description, unit='count'):
    return metric(identifier, label, status='unavailable', unit=unit, description=description,
                  source='Authoritative source not connected')


def _milestone_metrics(status, rows=None, as_of=None):
    known = status == 'available'
    return [
        metric('due_30d', 'Milestones due in 30 days',
               sum(as_of <= row.target_date <= as_of + timedelta(days=30) for row in rows) if known else None,
               status=status, source='Project milestone register',
               description='Incomplete milestones with recorded target dates from today through 30 days ahead.'),
        metric('overdue', 'Overdue milestones', sum(row.target_date < as_of for row in rows) if known else None,
               status=status, source='Project milestone register',
               description='Incomplete milestones with a recorded target date before today.'),
        _unavailable('at_risk', 'Milestones at risk', 'A governed readiness or risk assessment for individual milestones is not recorded.'),
        _unavailable('first_submission_acceptance', 'First submission acceptance', 'Approved submission and acceptance outcomes are not connected.', 'percent'),
    ]


def _empty(status, description):
    return {
        'status': status, 'scope': dict(SCOPE), 'source_updated_at': None, 'source_timestamp_kind': None,
        'kpis': [metric('active_projects', 'Active projects', status=status, description=description),
                 _contract_metric('contract_value', 'Open project contract value', [], description, status=status)]
                + [_unavailable(key, label, reason, unit) for key, label, unit, reason in GAPS],
        'register': {'status': status, 'projects': [], 'total_rows': None, 'returned_rows': 0,
                     'truncated': False, 'scope': dict(SCOPE), 'description': description},
        'health': {'status': status, 'counts': None, 'causes': [], 'description': description,
                   'metrics': [_contract_metric('contract_value_at_risk', 'Contract value requiring attention', [], description, status=status),
                               _unavailable('revenue_at_risk', 'Revenue at risk', 'A governed revenue exposure assessment is not connected.', 'currency')]},
        'actions': [], 'action_count': None, 'actions_returned': 0, 'actions_truncated': False,
        'actions_status': status,
        'milestones': {'status': status, 'rows': [], 'total_rows': None, 'returned_rows': 0,
                       'truncated': False, 'metrics': _milestone_metrics(status), 'source_updated_at': None,
                       'description': description},
        'delivery_capacity': {'status': 'unavailable', 'rows': [],
                              'description': 'Discipline-level approved demand and available delivery hours on a common reporting period are not connected; headcount is not capacity.'},
        'concentration': {'status': status, 'by_client': [], 'by_currency': [], 'description': description},
        'delivery_outlook': {'status': 'unavailable', 'series': [], 'scatter': [],
                             'description': 'Approved planned-versus-forecast completion history and comparable final margin and schedule-variance forecasts are not connected.'},
    }


def _milestones(projects, as_of):
    from apps.core.project_models import ProjectMilestone

    lookup = {project.pk: project for project in projects}
    source = ProjectMilestone.objects.filter(project_id__in=lookup, is_deleted=False)
    records = list(source.filter(is_completed=False).order_by('target_date', 'pk'))
    latest = source.aggregate(latest=Max('updated_at'))['latest']
    next_by_project, attention = {}, []
    for record in records:
        project = lookup[record.project_id]
        row = {'id': str(record.pk), 'project_id': str(project.pk), 'project_code': project.code,
               'project_name': project.name, 'name': record.name, 'due_date': record.target_date.isoformat(),
               'status': 'overdue' if record.target_date < as_of else 'due_today' if record.target_date == as_of else 'upcoming',
               'owner': None, 'project_owner': _owner(project), 'readiness': None,
               'route': _route(project.pk, 'plan-baseline')}
        next_by_project.setdefault(str(project.pk), row)
        if record.target_date <= as_of + timedelta(days=30):
            attention.append(row)
    return {
        'status': 'available', 'rows': attention[:50], 'total_rows': len(attention),
        'returned_rows': min(len(attention), 50), 'truncated': len(attention) > 50,
        'metrics': _milestone_metrics('available', records, as_of),
        'source_updated_at': latest.isoformat() if latest else None,
        'source_timestamp_kind': 'latest_record_update' if latest else None,
        'description': 'Incomplete recorded project milestones overdue or due in the next 30 days. The project owner is shown separately; milestone accountability and readiness are not recorded.',
    }, next_by_project


def _concentration(projects):
    clients = defaultdict(list)
    for project in projects:
        clients[project.client_name.strip() or 'Unassigned'].append(project)
    rows = []
    for label, items in sorted(clients.items()):
        money = _contract_metric('client_contracts', 'Client contracts', items, '')
        rows.append({'label': label, 'project_count': len(items), 'by_currency': money['by_currency'],
                     'incomplete_currencies': money['incomplete_currencies']})
    currencies = _contracts(projects)
    for group in currencies:
        items = [project for project in projects if _currency(project) == group['currency']]
        group.update({'top_client': None, 'top_five_projects': None})
        # Shares require a complete positive denominator and nonnegative values.
        if group['status'] != 'available' or Decimal(group['total']) <= 0 or any(p.contract_value < 0 for p in items):
            continue
        total = Decimal(group['total'])
        client_totals = defaultdict(Decimal)
        for project in items:
            client_totals[project.client_name.strip() or 'Unassigned'] += project.contract_value
        label, amount = sorted(client_totals.items(), key=lambda row: (-row[1], row[0]))[0]
        largest = sum(sorted((p.contract_value for p in items), reverse=True)[:5], Decimal('0'))
        # An unassigned client is a data-quality bucket, never a real top client.
        if all(project.client_name.strip() for project in items):
            group['top_client'] = {'label': label, 'amount': _decimal(amount),
                                   'share_pct': float((amount / total * 100).quantize(Decimal('0.01')))}
        group['top_five_projects'] = {'amount': _decimal(largest),
                                      'share_pct': float((largest / total * 100).quantize(Decimal('0.01')))}
    return {'status': 'partial' if any(row['status'] != 'available' for row in currencies) else 'available',
            'by_client': rows, 'by_currency': currencies,
            'description': 'Accessible open projects grouped by the recorded client-name text. Contract shares use complete currency-specific denominators; no FX conversion or legal-entity consolidation. Missing client names withhold top-client shares.'}


def build_portfolio_performance(user, context, project_section):
    if 'project_control' not in context['allowed_modules']:
        return _empty('restricted', 'Project Control read access is required.')
    result = _empty('error', 'The authorized project source could not be read.')
    try:
        from apps.project_control.access import accessible_enterprise_projects
        with transaction.atomic():
            projects = list(accessible_enterprise_projects(user).filter(status__in=OPEN_STATUSES)
                            .select_related('owner').order_by('code', 'pk'))
    except Exception:
        logger.exception('Executive portfolio project register unavailable')
        return result

    latest_update = max((project.updated_at for project in projects), default=None)
    result.update({'status': 'available', 'source_updated_at': latest_update.isoformat() if latest_update else None,
                   'source_timestamp_kind': 'latest_record_update' if latest_update else None,
                   'source_timestamp_label': 'Latest accessible open project register update'})
    result['kpis'][:2] = [
        metric('active_projects', 'Active projects', sum(project.status == 'active' for project in projects),
               source='Enterprise project register', route='/projects', description='Accessible projects currently marked active; planning and on-hold projects remain in the open-project register.'),
        _contract_metric('contract_value', 'Open project contract value', projects,
                         'Recorded awarded contract values of accessible planning, active and on-hold projects, kept by original currency. These values do not represent backlog or remaining revenue.'),
    ]
    governed = context.get('_portfolio_exception_report')
    governed_rows = {str(row['project']['id']): row for row in governed['projects']} if governed is not None else {}
    health_status = 'available' if governed is not None else 'error'
    health_counts = Counter({key: 0 for key in HEALTH_KEYS})
    causes, actions, register, at_risk = {}, [], [], []
    for project in projects:
        report = governed_rows.get(str(project.pk))
        health = report['overall_severity'] if report else 'unknown'
        if health not in HEALTH_KEYS:
            health = 'unknown'
        health_counts[health] += 1
        snapshot = report['latest_snapshot'] if report else None
        issues = report['exceptions'] if report else []
        project_causes = []
        for index, issue in enumerate(issues):
            category = issue['category']
            cause = causes.setdefault(category, {'projects': set(), 'exception_count': 0})
            cause['projects'].add(str(project.pk))
            cause['exception_count'] += 1
            project_causes.append({key: issue[key] for key in ['code', 'category', 'title', 'detail', 'severity']})
            decision = action(f"portfolio-{project.pk}-{issue['code']}-{index}", 'project_control',
                              f'{project.code}: {issue["title"]}', severity=issue['severity'],
                              owner=issue['owner']['name'], route=_route(project.pk, issue['target_view']), detail=issue['detail'])
            decision.update({'project_id': str(project.pk), 'project_name': project.name, 'impact': None})
            actions.append(decision)
        if health in {'critical', 'high'}:
            at_risk.append(project)
        register.append({
            'id': str(project.pk), 'code': project.code, 'name': project.name, 'status': project.status,
            'phase': None, 'progress_pct': project.progress, 'owner': _owner(project), 'health': health,
            'exception_count': len(issues) if report else None, 'causes': project_causes,
            'route': _route(project.pk), 'data_date': snapshot['data_date'].isoformat() if snapshot else None,
            'client_name': project.client_name, 'contract_value': _decimal(project.contract_value) if project.contract_value is not None else None,
            'currency': (project.currency or '').strip().upper() or None,
            'business_unit': None, 'schedule_variance': None, 'forecast_margin': None, 'next_milestone': None,
        })
    if health_counts['unknown']:
        health_status = 'partial' if governed is not None else 'error'
    result['health'] = {
        'status': health_status, 'counts': dict(health_counts, total=len(projects)),
        'causes': [{'category': category, 'label': category.replace('_', ' ').title(),
                    'project_count': len(data['projects']), 'exception_count': data['exception_count']}
                   for category, data in sorted(causes.items())],
        'description': 'Governed cost, schedule, reporting and control exceptions. Clear means no recorded exception, not verified delivery confidence. Cause counts overlap across projects.',
        'metrics': [_contract_metric('contract_value_at_risk', 'Contract value requiring attention', at_risk,
                                    'Full recorded contract values of open projects with high or critical governed exceptions; not forecast loss or revenue at risk.',
                                    status='available' if health_status == 'available' else 'error'),
                    _unavailable('revenue_at_risk', 'Revenue at risk', 'A governed revenue exposure assessment is not connected.', 'currency')],
    }
    if health_status != 'available':
        result['health']['description'] = 'Governed exception coverage is incomplete or failed; projects without source results remain unknown. Known counts do not establish overall delivery confidence.'
    actions.sort(key=lambda row: (SEVERITY_ORDER.get(row['severity'], 9), row['id']))
    result.update({'actions': actions[:50], 'action_count': len(actions) if health_status == 'available' else None,
                   'actions_returned': min(len(actions), 50), 'actions_truncated': len(actions) > 50,
                   'actions_status': health_status})
    try:
        with transaction.atomic():
            result['milestones'], next_milestones = _milestones(projects, timezone.localtime(context['generated_at']).date())
        for row in register:
            row['next_milestone'] = next_milestones.get(row['id'])
    except Exception:
        logger.exception('Executive portfolio milestone register unavailable')
        result['milestones']['description'] = 'The project milestone source could not be read.'
    result['register'] = {'status': 'available', 'projects': register[:200], 'total_rows': len(register),
                          'returned_rows': min(len(register), 200), 'truncated': len(register) > 200,
                          'scope': dict(SCOPE), 'description': 'Recorded open-project fields with governed exception causes. Phase, final margin and schedule variance are not available from an approved common source.'}
    result['concentration'] = _concentration(projects)
    if health_status != 'available' or result['milestones']['status'] == 'error':
        result['status'] = 'partial'
    return result
