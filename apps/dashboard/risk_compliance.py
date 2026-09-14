"""Verified assurance follow-ups, without manufacturing enterprise risk evidence.

The project quality register contains counters, not safety events or mitigation
items. Audit schedules contain statuses, not closed/open individual findings.
Disabled spot-check records and generated AI risk scores are deliberately unused.
"""
import logging
from datetime import timedelta

from django.apps import apps
from django.db import transaction
from django.utils import timezone

from .executive import SEVERITY_ORDER, action, metric


logger = logging.getLogger(__name__)
PROJECT_SOURCE = 'QHSE running project quality register'
AUDIT_SOURCE = 'QHSE audit schedule register'
QUALITY_ROUTE = '/qhse/general/quality'
HEADLINES = (
    ('high_enterprise_risks', 'High enterprise risks', 'count', 'An approved enterprise risk register and severity taxonomy are not connected. Project exceptions and quality counters are not enterprise risks.'),
    ('overdue_mitigations', 'Overdue mitigations', 'count', 'Individual risk mitigation actions with accountable owners, deadlines and closure evidence are not connected.'),
    ('recordable_qhse_incidents', 'Recordable QHSE incidents', 'count', 'A verified recordable incident register is not connected. Quality CARs and observations are not safety events.'),
    ('compliance_obligations_on_time', 'Compliance obligations on time', 'percent', 'A governed obligation register with deadlines and verified completion dates is not connected. QHSE audit schedules do not establish obligation timeliness.'),
    ('open_audit_findings', 'Open audit findings', 'count', 'Individual audit findings with distinct identities and closure states are not connected. Free-text audit findings do not provide an open-finding count.'),
)
QHSE_GAPS = (
    ('recordable_incidents', 'Recordable incidents', 'A verified recordable safety incident register is not connected.'),
    ('lost_time_injuries', 'Lost-time injuries', 'Verified injury classifications and lost-time evidence are not connected.'),
    ('near_misses', 'Near misses', 'A verified near-miss event register is not connected. Quality observations are not near misses.'),
    ('quality_nonconformances', 'Quality nonconformities', 'An enabled item-level nonconformance register is not connected; disabled spot checks are not exposed and project CAR counts are not substituted.'),
    ('environmental_events', 'Environmental events', 'A verified environmental event register is not connected.'),
    ('overdue_corrective_actions', 'Corrective actions overdue', 'Individual corrective-action due dates and closure states are not connected. A project-level CAR delay does not establish how many actions are overdue.'),
)
PROJECT_METRICS = (
    ('open_cars', 'Recorded open CARs', 'count', 'Sum of recorded open CAR counts on active QHSE projects; not distinct incident or enterprise mitigation records.'),
    ('delayed_car_projects', 'Projects with delayed CAR closure', 'projects', 'Active QHSE project records with a positive open-CAR count and a positive recorded closure delay. This is a project count, not the count of overdue actions.'),
    ('open_observations', 'Recorded open observations', 'count', 'Sum of recorded open quality observations on active QHSE projects; not safety near misses.'),
    ('delayed_observation_projects', 'Projects with delayed observations', 'projects', 'Active QHSE project records with open observations and a positive recorded closure delay.'),
    ('delayed_audit_projects', 'Projects reporting audit delays', 'projects', 'Active QHSE project records with a positive recorded audit delay. This is separate from the individual QHSE audit schedule.'),
)


def _gap(key, label, description, unit='count'):
    return metric(key, label, status='unavailable', unit=unit, description=description,
                  source='Governed source not connected')


def _source(identifier, status, route, description, updated=None):
    return {'id': identifier, 'status': status, 'route': route,
            'source_updated_at': updated.isoformat() if updated else None,
            'source_timestamp_kind': 'latest_record_update' if updated else None,
            'description': description}


def _empty_projects(status, description, route=None):
    return {'status': status, 'route': route, 'description': description, 'rows': [],
            'metrics': [metric(key, label, status=status, unit=unit, source=PROJECT_SOURCE,
                               route=route, description=description if status != 'available' else reason)
                        for key, label, unit, reason in PROJECT_METRICS],
            'source': _source('project_quality', status, route, description)}


def _project_quality(route):
    project = apps.get_model('qhse', 'QHSERunningProject')
    fields = ['id', 'project_no', 'project_title', 'project_manager', 'cars_open',
              'cars_delayed_closing_no_days', 'obs_open', 'obs_delayed_closing_no_days',
              'delay_in_audits_no_days', 'updated_at']
    rows = list(project.objects.filter(is_active=True).values(*fields).order_by('project_no', 'pk'))
    if not rows:
        return _empty_projects('unavailable', 'The active QHSE project register contains no records; quality coverage is not established.', route)
    numeric = ['cars_open', 'cars_delayed_closing_no_days', 'obs_open', 'obs_delayed_closing_no_days', 'delay_in_audits_no_days']
    if any(row[field] is None or row[field] < 0 for row in rows for field in numeric):
        raise ValueError('Invalid project quality counter')
    totals = {
        'open_cars': sum(row['cars_open'] for row in rows),
        'delayed_car_projects': sum(row['cars_open'] > 0 and row['cars_delayed_closing_no_days'] > 0 for row in rows),
        'open_observations': sum(row['obs_open'] for row in rows),
        'delayed_observation_projects': sum(row['obs_open'] > 0 and row['obs_delayed_closing_no_days'] > 0 for row in rows),
        'delayed_audit_projects': sum(row['delay_in_audits_no_days'] > 0 for row in rows),
    }
    followups = []
    for row in rows:
        conditions = [
            ('car-delay', row['cars_open'] > 0 and row['cars_delayed_closing_no_days'] > 0,
             'Review delayed CAR closure', f"{row['cars_open']} recorded open CARs; project-level closure delay {row['cars_delayed_closing_no_days']} days. Individual action deadlines and overdue item counts are not recorded.", 'high'),
            ('observation-delay', row['obs_open'] > 0 and row['obs_delayed_closing_no_days'] > 0,
             'Review delayed quality observations', f"{row['obs_open']} recorded open observations; project-level closure delay {row['obs_delayed_closing_no_days']} days. These are quality observations, not safety near misses.", 'medium'),
            ('audit-delay', row['delay_in_audits_no_days'] > 0,
             'Review reported project audit delay', f"The project quality record reports an audit delay of {row['delay_in_audits_no_days']} days. The affected audit and its target date are not identified in this project-level counter.", 'medium'),
        ]
        for key, applies, title, detail, severity in conditions:
            if applies:
                followups.append({'id': f"quality-{row['id']}-{key}", 'source_type': 'project_quality',
                                  'source': PROJECT_SOURCE, 'project_code': row['project_no'], 'project_name': row['project_title'],
                                  'category': 'quality', 'title': title, 'detail': detail,
                                  'recorded_owner': row['project_manager'] or None, 'owner_label': 'Project manager',
                                  'status': 'reported_delay', 'due_date': None, 'route': route, 'severity': severity})
    description = 'Active shared QHSE project records. Quality counts and reported delays are kept separate from safety events, item-level mitigation and enterprise risk ratings.'
    return {'status': 'available', 'route': route, 'description': description, 'rows': followups,
            'metrics': [metric(key, label, totals[key], unit=unit, source=PROJECT_SOURCE, route=route, description=reason)
                        for key, label, unit, reason in PROJECT_METRICS],
            'source': _source('project_quality', 'available', route, description, max(row['updated_at'] for row in rows))}


def _empty_audits(status, description, as_of, route=None):
    metrics = [metric(key, label, status=status, source=AUDIT_SOURCE, route=route, description=description)
               for key, label in [('scheduled_audits', 'Scheduled QHSE audits'), ('delayed_audits', 'QHSE audits marked delayed'), ('completed_audits', 'QHSE audits marked completed')]]
    metrics += [_gap('open_audit_findings', 'Open audit findings', HEADLINES[-1][3]),
                _gap('control_effectiveness', 'Control effectiveness', 'Verified control tests, outcomes and an agreed denominator are not connected.', 'percent')]
    return {'status': status, 'route': route, 'description': description, 'metrics': metrics, 'rows': [],
            'calendar': {'status': status, 'basis': 'qhse_audit_schedule', 'rows': [], 'total_rows': None,
                         'returned_rows': 0, 'truncated': False, 'route': route,
                         'period_end': (as_of + timedelta(days=30)).isoformat(), 'description': description},
            'source': _source('qhse_audits', status, route, description)}


def _audits(as_of):
    audit = apps.get_model('qhse', 'QHSEAudit')
    # Existing audit read access is shared. Keep only audits of active QHSE
    # projects, and never select free-text findings, remarks or incident details.
    records = list(audit.objects.filter(project__is_active=True).values(
        'id', 'project__project_no', 'project__project_title', 'audit_type', 'audit_number',
        'audit_date', 'auditor', 'status', 'updated_at',
    ).order_by('audit_date', 'pk'))
    description = 'Recorded QHSE audit statuses for active QHSE projects. Scheduled dates and completed status do not establish completion timeliness, individual finding closure or statutory compliance.'
    result = _empty_audits('available', description, as_of, QUALITY_ROUTE)
    for item, state in zip(result['metrics'][:3], ['SCHEDULED', 'DELAYED', 'COMPLETED']):
        item.update({'value': sum(row['status'] == state for row in records), 'reason': None,
                     'description': f'QHSE audit records for active projects currently marked {state.lower()}; current register count, not a time-period completion or finding count.'})
        item['definition'] = item['description']
    calendar, followups = [], []
    for record in records:
        pending = record['status'] in {'SCHEDULED', 'DELAYED'}
        row = {'id': f"audit-{record['id']}", 'title': f"{record['audit_type'].title()} audit {record['audit_number']}",
               'project_code': record['project__project_no'], 'project_name': record['project__project_title'],
               'date': record['audit_date'].isoformat(),
               'date_status': 'past_target_date' if record['audit_date'] < as_of else 'due_today' if record['audit_date'] == as_of else 'upcoming',
               'status': record['status'], 'owner': None, 'recorded_auditor': record['auditor'] or None,
               'source': AUDIT_SOURCE, 'route': QUALITY_ROUTE}
        if pending and record['audit_date'] <= as_of + timedelta(days=30):
            calendar.append(row)
        if record['status'] == 'DELAYED' or (record['status'] == 'SCHEDULED' and record['audit_date'] < as_of):
            detail = ('The audit is recorded as delayed. Confirm the next audit target and update the governed schedule.' if record['status'] == 'DELAYED'
                      else 'The scheduled audit target date has passed and its status remains scheduled. Verify the source status; no open-finding count or statutory breach is inferred.')
            followups.append({'id': row['id'], 'source_type': 'qhse_audit', 'source': AUDIT_SOURCE,
                              'project_code': row['project_code'], 'project_name': row['project_name'], 'category': 'audit_schedule',
                              'title': row['title'], 'detail': detail, 'recorded_owner': row['recorded_auditor'], 'owner_label': 'Auditor',
                              'status': 'reported_delay' if record['status'] == 'DELAYED' else 'past_target_date',
                              'due_date': row['date'], 'route': QUALITY_ROUTE, 'severity': 'medium'})
    result['rows'] = followups
    result['calendar'].update({'rows': calendar[:50], 'total_rows': len(calendar),
                               'returned_rows': min(len(calendar), 50), 'truncated': len(calendar) > 50,
                               'description': 'Scheduled or delayed QHSE audits of active projects with recorded dates through the next 30 days, including past targets. These are audit schedule records, not a compliance obligation register. Auditor is shown separately from unrecorded action accountability.'})
    result['source'] = _source('qhse_audits', 'available', QUALITY_ROUTE, description,
                               max((row['updated_at'] for row in records), default=None))
    return result


def _combined_status(states):
    if all(state == 'available' for state in states):
        return 'available'
    if 'available' in states:
        return 'partial'
    if 'error' in states:
        return 'error'
    if all(state == 'restricted' for state in states):
        return 'restricted'
    return 'unavailable'


def build_risk_compliance(user, context):
    del user  # Effective read decisions retain each shared source's access scope.
    allowed = context['allowed_modules']
    as_of = timezone.localtime(context['generated_at']).date()
    projects = _empty_projects('restricted', 'A separate QHSE overview, detailed or quality read grant is required.')
    audits = _empty_audits('restricted', 'The separate QHSE Quality read grant is required for the audit register.', as_of)
    if allowed.intersection({'qhse', 'qhse_detailed', 'qhse_quality'}):
        route = QUALITY_ROUTE if 'qhse_quality' in allowed else '/qhse/general/detailed' if 'qhse_detailed' in allowed else '/qhse'
        try:
            with transaction.atomic():
                projects = _project_quality(route)
        except LookupError:
            projects = _empty_projects('unavailable', 'The project quality source is not configured.', route)
        except Exception:
            logger.exception('Executive risk/compliance project quality source failed')
            projects = _empty_projects('error', 'The project quality source could not be read or its counters are invalid.', route)
    if 'qhse_quality' in allowed:
        try:
            with transaction.atomic():
                audits = _audits(as_of)
        except LookupError:
            audits = _empty_audits('unavailable', 'The QHSE audit source is not configured.', as_of, QUALITY_ROUTE)
        except Exception:
            logger.exception('Executive risk/compliance audit source failed')
            audits = _empty_audits('error', 'The QHSE audit source could not be read.', as_of, QUALITY_ROUTE)
    sources = [projects['source'], audits['source']]
    state = _combined_status([row['status'] for row in sources])
    has_available = any(row['status'] == 'available' for row in sources)
    followups = projects['rows'] + audits['rows']
    followups.sort(key=lambda row: (SEVERITY_ORDER.get(row['severity'], 9), row['due_date'] or '9999', row['id']))
    actions = []
    for row in followups:
        item = action(f"risk-source-{row['id']}", 'qhse', f"{row['project_code']}: {row['title']}",
                      owner='QHSE', severity=row['severity'], route=row['route'], detail=row['detail'])
        item.update({'source_type': row['source_type'], 'project_code': row['project_code'], 'impact': None,
                     'source_target_date': row['due_date']})
        # An audit target is not an executive intervention deadline.
        actions.append(item)
    timestamps = [row['source_updated_at'] for row in sources if row['source_updated_at'] is not None]
    latest = max(timestamps, default=None)
    qhse = {'status': projects['status'], 'route': projects['route'], 'description': projects['description'],
            'metrics': [_gap(key, label, reason) for key, label, reason in QHSE_GAPS],
            'project_metrics': projects['metrics'], 'source_updated_at': projects['source']['source_updated_at'],
            'source_timestamp_kind': projects['source']['source_timestamp_kind']}
    audit_controls = {key: audits[key] for key in ['status', 'route', 'description', 'metrics']}
    audit_controls.update({key: audits['source'][key] for key in ['source_updated_at', 'source_timestamp_kind']})
    audits['calendar'].update({key: audits['source'][key] for key in ['source_updated_at', 'source_timestamp_kind']})
    return {
        'status': state, 'scope': {'label': 'Authorized QHSE assurance source records for active projects',
                                  'period': 'current_snapshot', 'consolidated': False},
        'source_updated_at': latest, 'source_timestamp_kind': 'latest_record_update' if latest else None,
        'source_timestamp_label': 'Latest authorized assurance source record update',
        'kpis': [_gap(key, label, reason, unit) for key, label, unit, reason in HEADLINES],
        'register': {'status': 'unavailable', 'risks': [], 'total_rows': None, 'returned_rows': 0, 'truncated': False,
                     'description': 'An approved enterprise risk register is not connected. Residual risk, likelihood, impact, appetite, treatment progress and review dates are not inferred from operational exceptions.'},
        'source_followups': {'status': state, 'rows': followups[:200], 'total_rows': len(followups) if has_available else None,
                             'returned_rows': min(len(followups), 200), 'truncated': len(followups) > 200,
                             'coverage_complete': state == 'available',
                             'description': 'Recorded quality and audit-schedule follow-ups from available authorized sources. Counts cover source records, which can overlap; they are not distinct enterprise risks. Restricted, missing or failed sources have incomplete coverage.'},
        'actions': actions[:50], 'actions_status': state, 'action_count': len(actions) if has_available else None,
        'actions_returned': min(len(actions), 50), 'actions_truncated': len(actions) > 50,
        'coverage_complete': state == 'available',
        'qhse_performance': qhse, 'compliance_calendar': audits['calendar'], 'audit_controls': audit_controls,
        'risk_concentration': {'status': 'unavailable', 'rows': [], 'by_currency': [],
                               'description': 'Comparable assessed enterprise exposures and approved currency bases are not connected. Project counts and CAR counts are not financial risk concentration.'},
        'heatmap': {'status': 'unavailable', 'cells': [],
                    'description': 'A governed likelihood/impact scale and assessed enterprise risks are required. Project quality flags cannot populate a risk heatmap.'},
        'risk_movement': {'status': 'unavailable', 'series': [],
                          'description': 'Comparable dated enterprise risk assessments are not connected. Current counters cannot reconstruct residual-risk movement.'},
        'mitigation_effectiveness': {'status': 'unavailable', 'rows': [], 'metrics': [],
                                   'description': 'Verified mitigation outcomes and comparable pre/post-treatment assessments are not connected. Closed CAR counters do not prove mitigation effectiveness.'},
        'sources': sources,
    }
