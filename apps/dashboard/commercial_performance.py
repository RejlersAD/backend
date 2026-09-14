"""Commercial tab derived from the visible CRM opportunity register.

This read model does not infer approved revenue, framework backlog, submission
readiness, resource commitments or a historical pipeline from current records.
"""
import logging
from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.db import transaction
from django.utils import timezone

from .executive import SEVERITY_ORDER, _visible, action, metric


logger = logging.getLogger(__name__)
OPEN_STAGES = ('lead', 'qualified', 'proposal', 'negotiation', 'award_pending')
QUALIFIED_STAGES = OPEN_STAGES[1:]
SCOPE = {'label': 'Authorized open CRM opportunities; original currencies remain separate',
         'open_stages': list(OPEN_STAGES), 'qualified_stages': list(QUALIFIED_STAGES)}
PROBABILITY_BASIS = 'Stored CRM probability; normally assigned from the recorded stage.'
QUALITY = (
    ('missing_owner', 'Missing opportunity owner', 'Open opportunities without a recorded owner.'),
    ('missing_next_action', 'Missing next action', 'Open opportunities missing next-action text or its scheduled date.'),
    ('missing_submission_date', 'Missing submission target', 'Current proposal-stage opportunities with no recorded submission target date.'),
    ('invalid_probability', 'Invalid probability', 'Open opportunities whose stored CRM probability is missing or outside 0 to 100.'),
    ('weighted_value_mismatches', 'Inconsistent weighted value', 'Stored weighted values that do not reconcile to estimated value times a valid stored probability, rounded to currency precision.'),
)


def _route(deal_id):
    return '/sales/opportunities?' + urlencode({'record': str(deal_id)})


def _currency(deal):
    return (deal.currency or '').strip().upper() or 'UNSPECIFIED'


def _money(value):
    return str(value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)) if value is not None else None


def _valid_probability(deal):
    return deal.probability is not None and 0 <= deal.probability <= 100


def _weighted_mismatch(deal):
    if deal.estimated_value is None or deal.weighted_value is None or not _valid_probability(deal):
        return False
    expected = deal.estimated_value * Decimal(deal.probability) / 100
    # Database backends may differ at an exact half-cent tie. Accept either
    # correctly rounded tie, but never silently repair a material mismatch.
    return abs(deal.weighted_value - expected) > Decimal('0.005')


def _amount_groups(deals, field, *, currency_scope=None):
    # Empty eligible cohorts may report zero in a known, visible source currency.
    # An unknown currency is never silently relabelled or combined as real money.
    groups = {code: {'count': 0, 'total': Decimal('0'), 'missing_value_count': 0, 'invalid_value_count': 0}
              for code in (currency_scope or []) if code != 'UNSPECIFIED'}
    for deal in deals:
        code = _currency(deal)
        group = groups.setdefault(code, {'count': 0, 'total': Decimal('0'), 'missing_value_count': 0, 'invalid_value_count': 0})
        group['count'] += 1
        value = getattr(deal, field)
        if value is None:
            group['missing_value_count'] += 1
        elif field == 'weighted_value' and (not _valid_probability(deal) or _weighted_mismatch(deal) or deal.estimated_value is None):
            group['invalid_value_count'] += 1
        else:
            group['total'] += value
    rows = []
    for code, group in sorted(groups.items()):
        complete = code != 'UNSPECIFIED' and not group['missing_value_count'] and not group['invalid_value_count']
        rows.append({**group, 'currency': code, 'status': 'available' if complete else 'incomplete',
                     'total': _money(group['total']) if complete else None})
    return rows


def _money_metric(identifier, label, deals, field, description, *, status='available', currency_scope=None):
    groups = _amount_groups(deals, field, currency_scope=currency_scope) if status == 'available' else []
    incomplete = [row['currency'] for row in groups if row['status'] == 'incomplete']
    result = metric(identifier, label, unit='currency', status='partial' if incomplete else status,
                    source='CRM opportunity register', route=None if status == 'restricted' else '/sales/opportunities',
                    by_currency=[{'currency': row['currency'], 'amount': row['total']} for row in groups if row['status'] == 'available'],
                    description=description)
    result.update({'incomplete_currencies': incomplete,
                   'missing_value_count': sum(row['missing_value_count'] for row in groups) if status == 'available' else None,
                   'invalid_value_count': sum(row['invalid_value_count'] for row in groups) if status == 'available' else None})
    if incomplete:
        result['reason'] = 'Affected currency totals are withheld because currency, amount or valid weighting inputs are missing or inconsistent.'
    return result


def _gap(identifier, label, reason, unit='currency'):
    return metric(identifier, label, status='unavailable', unit=unit, description=reason,
                  source='Authoritative reporting source not connected')


def _empty(status, description):
    return {
        'status': status, 'scope': dict(SCOPE), 'currencies': [], 'source_updated_at': None,
        'source_timestamp_kind': None, 'source_timestamp_label': 'Latest authorized CRM opportunity update',
        'kpis': [
            _money_metric('qualified_pipeline', 'Qualified pipeline', [], 'estimated_value', description, status=status),
            _money_metric('weighted_pipeline', 'Weighted pipeline', [], 'weighted_value', description, status=status),
            metric('win_rate', 'Win rate', unit='percent', status=status, description=description),
            metric('proposals_due_30d', 'Proposals due in 30 days', status=status, description=description),
            _gap('framework_backlog', 'Framework backlog', 'Signed remaining call-off obligations are not connected; framework ceilings and unutilized agreement value are not backlog.'),
        ],
        'register': {'status': status, 'opportunities': [], 'total_rows': None, 'total_rows_by_currency': {},
                     'returned_rows': 0, 'truncated': False, 'scope': dict(SCOPE), 'description': description},
        'actions': [], 'actions_status': status, 'action_count': None, 'actions_returned': 0, 'actions_truncated': False,
        'bid_calendar': {'status': status, 'rows': [], 'total_rows': None, 'total_rows_by_currency': {},
                         'returned_rows': 0, 'truncated': False, 'description': description},
        'client_concentration': {'status': status, 'by_currency': [], 'description': description},
        'commercial_quality': {'status': status, 'description': description,
                               'metrics': [metric(key, label, status=status, description=reason, source='CRM opportunity register') for key, label, reason in QUALITY]},
        'resource_demand': {'status': 'unavailable', 'rows': [],
                            'description': 'Approved discipline-level demand and delivery capacity on a common period are not connected. Estimated opportunity hours alone do not establish resource commitments or utilisation.'},
        'pipeline_outlook': {'status': 'unavailable', 'series': [],
                             'description': 'An approved forecast series with authorized opportunity coverage and original currencies is not connected. Expected close dates are CRM targets, not recognized revenue forecasts.'},
        'pipeline_movement': {'status': 'unavailable', 'series': [],
                              'description': 'Opening and closing currency-specific pipeline snapshots with historical opportunity values are not connected. Current stages cannot establish a monetary movement bridge.'},
        'pipeline_stages': [],
        'won_handoff': {'status': status, 'count': None, 'route': None, 'rows': [], 'description': description},
    }


def _win_rate(deals, as_of):
    start = as_of - timedelta(days=364)
    outcomes = [deal for deal in deals if deal.stage in {'awarded', 'converted', 'lost'}]
    undated = sum(deal.actual_close_date is None for deal in outcomes)
    future = sum(deal.actual_close_date is not None and deal.actual_close_date > as_of for deal in outcomes)
    closed = [deal for deal in outcomes if deal.actual_close_date is not None and start <= deal.actual_close_date <= as_of]
    won = sum(deal.stage in {'awarded', 'converted'} for deal in closed)
    value = round(won / len(closed) * 100, 2) if closed and not undated and not future else None
    result = metric('win_rate', 'Win rate', value, unit='percent', status='available' if value is not None else 'unavailable',
                    source='Recorded CRM outcomes and actual close dates', route='/sales/opportunities',
                    description='Current awarded or converted opportunities divided by awarded, converted and lost opportunities with recorded actual close dates in the trailing 365 calendar days. No-bid and cancelled records are excluded; all original currencies are counted together.')
    result.update({'period': 'trailing_365_days', 'period_start': start.isoformat(), 'period_end': as_of.isoformat(),
                   'numerator': won, 'denominator': len(closed), 'missing_close_date_count': undated, 'future_close_date_count': future})
    if value is None:
        result['reason'] = ('Missing or future actual close dates prevent a complete dated outcome cohort.' if undated or future
                            else 'No recorded won or lost outcomes fall within the trailing 365 calendar days; the ratio is undefined.')
    return result


def _quality_counts(deals):
    return {
        'missing_owner': sum(deal.owner_id is None for deal in deals),
        'missing_next_action': sum(not deal.next_action.strip() or deal.next_action_date is None for deal in deals),
        'missing_submission_date': sum(deal.stage == 'proposal' and deal.submission_due_date is None for deal in deals),
        'invalid_probability': sum(not _valid_probability(deal) for deal in deals),
        'weighted_value_mismatches': sum(_weighted_mismatch(deal) for deal in deals),
    }


def _flags(deal, as_of):
    flags = []
    def add(code, title, detail, severity='medium'):
        flags.append({'code': code, 'title': title, 'detail': detail, 'severity': severity})
    if deal.stage in QUALIFIED_STAGES and deal.bid_decision == 'pending':
        add('bid_decision_pending', 'Review bid decision', 'The opportunity has a recorded pending bid/no-bid decision.')
    if deal.award_status == 'pending':
        add('award_approval_pending', 'Review award approval', 'The recorded award is awaiting approval.', 'high')
    if deal.stage == 'proposal' and deal.submission_due_date and deal.submission_due_date < as_of:
        add('submission_target_passed', 'Verify proposal submission status', 'The recorded submission target has passed. This register does not establish whether the proposal was already submitted.', 'high')
    if deal.next_action_date and deal.next_action_date < as_of:
        add('next_action_date_passed', 'Review next action', 'The recorded CRM next-action date has passed; confirm the action outcome and update the register.')
    if deal.risk_level in {'high', 'critical'}:
        add('recorded_commercial_risk', 'Review recorded commercial risk', 'The opportunity risk field is marked high or critical; it is not a calculated loss forecast.', deal.risk_level)
    if deal.owner_id is None:
        add('missing_owner', 'Assign an opportunity owner', 'No opportunity owner is recorded.')
    if not deal.next_action.strip() or deal.next_action_date is None:
        add('missing_next_action', 'Record the next action', 'Next-action text or its scheduled date is missing.')
    if not _valid_probability(deal) or _weighted_mismatch(deal):
        add('invalid_weighting', 'Review pipeline weighting', 'The stored probability or weighted value is invalid or inconsistent; affected weighted currency totals are withheld.', 'high')
    return flags


def _concentration(deals):
    groups = _amount_groups(deals, 'estimated_value')
    for group in groups:
        cohort = [deal for deal in deals if _currency(deal) == group['currency']]
        clients = {}
        for deal in cohort:
            entry = clients.setdefault(str(deal.client_id), {'id': str(deal.client_id), 'label': deal.client.company_name,
                                                            'amount': Decimal('0'), 'opportunity_count': 0})
            entry['opportunity_count'] += 1
            if deal.estimated_value is not None:
                entry['amount'] += deal.estimated_value
        shareable = (group['status'] == 'available' and Decimal(group['total']) > 0
                     and all(deal.estimated_value >= 0 and deal.client.company_name.strip() for deal in cohort))
        ordered = sorted(clients.values(), key=lambda row: (-row['amount'], row['id']))
        for row in ordered:
            row['share_pct'] = float((row['amount'] / Decimal(group['total']) * 100).quantize(Decimal('0.01'))) if shareable else None
            row['amount'] = _money(row['amount']) if group['status'] == 'available' else None
        group['clients'] = ordered
        group['top_client_share'] = ordered[0]['share_pct'] if ordered else None
    return {'status': 'partial' if any(row['status'] == 'incomplete' for row in groups) else 'available',
            'by_currency': groups,
            'description': 'Estimated values of all visible open opportunities, grouped by CRM client identity within each original currency. Shares require a complete positive denominator, nonnegative values and recorded client names; these are pipeline concentrations, not revenue.'}


def _read_deals(user):
    from apps.sales.models import Deal
    fields = ['id', 'deal_code', 'deal_name', 'stage', 'estimated_value', 'weighted_value', 'currency', 'probability',
              'actual_close_date', 'expected_close_date', 'submission_due_date', 'next_action_date', 'next_action',
              'owner_id', 'client_id', 'risk_level', 'bid_decision', 'award_status', 'converted_project_id', 'updated_at',
              'owner__first_name', 'owner__last_name', 'owner__email', 'owner__username', 'client__company_name']
    return list(_visible(Deal.objects.select_related('client', 'owner').only(*fields), user, 'sales', 'owner').order_by('deal_code', 'pk'))


def build_commercial_performance(user, context):
    if 'sales_opportunities' not in context['allowed_modules']:
        return _empty('restricted', 'Sales opportunity read access is required.')
    try:
        with transaction.atomic():
            return _build_commercial(user, context)
    except Exception:
        logger.exception('Executive commercial opportunity source unavailable')
        return _empty('error', 'The authorized CRM opportunity source could not be read.')


def _build_commercial(user, context):
    from apps.sales.models import DEAL_STAGES
    deals = _read_deals(user)
    open_deals = [deal for deal in deals if deal.stage in OPEN_STAGES]
    qualified = [deal for deal in open_deals if deal.stage in QUALIFIED_STAGES]
    as_of = timezone.localtime(context['generated_at']).date()
    currencies = sorted({_currency(deal) for deal in open_deals})
    result = _empty('available', 'Authorized current CRM opportunity register.')
    latest = max((deal.updated_at for deal in deals), default=None)
    result.update({'currencies': currencies, 'source_updated_at': latest.isoformat() if latest else None,
                   'source_timestamp_kind': 'latest_record_update' if latest else None})
    due = [deal for deal in open_deals if deal.stage == 'proposal' and deal.submission_due_date is not None
           and as_of <= deal.submission_due_date <= as_of + timedelta(days=30)]
    result['kpis'][:4] = [
        _money_metric('qualified_pipeline', 'Qualified pipeline', qualified, 'estimated_value',
                      'Recorded estimated opportunity values in qualified, proposal, negotiation and award-pending stages. Leads and closed outcomes are excluded; each original currency remains separate.', currency_scope=currencies),
        _money_metric('weighted_pipeline', 'Weighted pipeline', open_deals, 'weighted_value',
                      'Stored weighted values across lead, qualified, proposal, negotiation and award-pending opportunities. Weighting normally follows configured stage probabilities and is not an approved revenue forecast.', currency_scope=currencies),
        _win_rate(deals, as_of),
        metric('proposals_due_30d', 'Proposals due in 30 days', len(due), source='CRM opportunity submission target dates', route='/sales/opportunities',
               description='Current proposal-stage opportunities with recorded submission target dates from today through 30 days ahead, across all currencies. This counts target dates, not confirmed unsubmitted proposal versions.'),
    ]
    register, actions, calendar = [], [], []
    for deal in open_deals:
        owner = (deal.owner.get_full_name() or deal.owner.email or deal.owner.username) if deal.owner else 'Unassigned'
        flags = _flags(deal, as_of)
        row = {'id': str(deal.pk), 'code': deal.deal_code, 'name': deal.deal_name,
               'client_id': str(deal.client_id), 'client_name': deal.client.company_name,
               'stage': deal.stage, 'stage_label': DEAL_STAGES[deal.stage]['name'],
               'estimated_value': _money(deal.estimated_value), 'weighted_value': _money(deal.weighted_value),
               'currency': _currency(deal), 'probability': deal.probability if _valid_probability(deal) else None,
               'probability_basis': PROBABILITY_BASIS, 'probability_evidence': None,
               'submission_due_date': deal.submission_due_date.isoformat() if deal.submission_due_date else None,
               'submission_status': None, 'expected_close_date': deal.expected_close_date.isoformat() if deal.expected_close_date else None,
               'expected_award_date': None, 'owner': owner, 'bid_owner': None, 'business_unit': None,
               'risk_level': deal.risk_level, 'health': 'attention' if flags else 'no_recorded_flags', 'flags': flags,
               'route': _route(deal.pk)}
        register.append(row)
        for flag in flags:
            item = action(f"commercial-{deal.pk}-{flag['code']}", 'sales', f'{deal.deal_code}: {flag["title"]}',
                          severity=flag['severity'], owner=owner, route=row['route'], detail=flag['detail'])
            item.update({'opportunity_id': str(deal.pk), 'opportunity_name': deal.deal_name, 'impact': None, 'currency': row['currency']})
            actions.append(item)
        if deal.stage == 'proposal' and deal.submission_due_date and deal.submission_due_date <= as_of + timedelta(days=30):
            calendar.append({key: row[key] for key in ['id', 'code', 'name', 'client_name', 'estimated_value', 'currency', 'owner', 'bid_owner', 'route']} | {
                'opportunity_id': row['id'], 'due_date': deal.submission_due_date.isoformat(), 'readiness': None,
                'status': 'past_target_date' if deal.submission_due_date < as_of else 'due_today' if deal.submission_due_date == as_of else 'upcoming'})
    result['register'] = {'status': 'available', 'opportunities': register[:200], 'total_rows': len(register),
                          'total_rows_by_currency': dict(Counter(row['currency'] for row in register)),
                          'returned_rows': min(len(register), 200), 'truncated': len(register) > 200, 'scope': dict(SCOPE),
                          'description': 'Recorded open opportunities. Probability is stored CRM weighting, not a validated prediction. Health reflects listed register flags; no recorded flags does not imply bid readiness or likelihood of winning.'}
    actions.sort(key=lambda row: (SEVERITY_ORDER.get(row['severity'], 9), row['id']))
    result.update({'actions': actions[:50], 'actions_status': 'available', 'action_count': len(actions),
                   'actions_returned': min(len(actions), 50), 'actions_truncated': len(actions) > 50})
    calendar.sort(key=lambda row: (row['due_date'], row['id']))
    result['bid_calendar'] = {'status': 'available', 'rows': calendar[:50], 'total_rows': len(calendar),
                              'total_rows_by_currency': dict(Counter(row['currency'] for row in calendar)),
                              'returned_rows': min(len(calendar), 50), 'truncated': len(calendar) > 50,
                              'description': 'Recorded submission targets of current proposal-stage opportunities, including past dates and dates through the next 30 days. A passed target does not prove non-submission. Readiness and dedicated bid accountability are not connected; owners are opportunity owners.'}
    counts = _quality_counts(open_deals)
    result['commercial_quality'] = {'status': 'available', 'description': 'Specific field completeness and weighting checks across all visible open CRM opportunities and all currencies. These checks do not establish commercial approval, legal review or bid quality.',
                                     'metrics': [metric(key, label, counts[key], source='CRM opportunity register', route='/sales/opportunities', description=reason) for key, label, reason in QUALITY]}
    result['client_concentration'] = _concentration(open_deals)
    for stage in OPEN_STAGES:
        cohort = [deal for deal in open_deals if deal.stage == stage]
        estimated = _amount_groups(cohort, 'estimated_value', currency_scope=currencies)
        weighted = {row['currency']: row for row in _amount_groups(cohort, 'weighted_value', currency_scope=currencies)}
        result['pipeline_stages'].append({'stage': stage, 'label': DEAL_STAGES[stage]['name'], 'count': len(cohort),
                                         'by_currency': [{'currency': row['currency'], 'amount': row['total'], 'weighted_amount': weighted[row['currency']]['total']}
                                                         for row in estimated if row['status'] == 'available' and weighted[row['currency']]['status'] == 'available'],
                                         'incomplete_currencies': [row['currency'] for row in estimated if row['status'] != 'available' or weighted[row['currency']]['status'] != 'available']})
    result['won_handoff'] = {'status': 'available', 'count': sum(deal.stage == 'awarded' and deal.award_status == 'approved' and deal.converted_project_id is None for deal in deals),
                             'route': '/sales/project-handovers' if 'sales_handovers' in context['allowed_modules'] else None,
                             'rows': [], 'description': 'Recorded approved awards without a linked converted project, across all visible currencies. This is not a count of accepted handovers or a readiness assessment; opening the governed handover workspace requires its separate read grant.'}
    return result
