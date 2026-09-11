"""RADAI platform engagement, including page visits; provider use is supporting telemetry."""
from datetime import timezone as utc

from django.db.models import Count, Q
from django.db.models.functions import TruncDate

from .ai_champion_models import ActivityEvent, AIUsageLog
from .ai_measurement_models import AIWorkflowRun


def activity_days(start, end, user_ids=None, organization_id=None):
    """One source per user/module/UTC day avoids counting API fan-out as extra use.

    Prefer recorded RADAI events; fall back to workflows, then historical provider
    records when that day/module has no platform event. Counts remain engagement,
    not tasks completed or verified productivity. Raw event metadata is not exposed.
    """
    events = ActivityEvent.objects.filter(timestamp__gte=start, timestamp__lt=end)
    workflows = AIWorkflowRun.objects.filter(started_at__gte=start, started_at__lt=end)
    calls = AIUsageLog.objects.filter(timestamp__gte=start, timestamp__lt=end).exclude(provenance='client')
    if user_ids is not None:
        events, workflows, calls = (query.filter(user_id__in=user_ids) for query in (events, workflows, calls))
    if organization_id is not None:
        events = events.filter(user__rbac_profile__organization_id=organization_id)
        workflows = workflows.filter(organization_id=organization_id)
        calls = calls.filter(Q(workflow__organization_id=organization_id) |
                             Q(workflow__isnull=True, user__rbac_profile__organization_id=organization_id))
    sources = [
        (events, 'timestamp', 'application', Q(success=True), 'RADAI activity'),
        (workflows, 'started_at', 'module', Q(status__in=['completed', 'returned']), 'RADAI workflow'),
        (calls, 'timestamp', 'application', Q(success=True), 'RADAI provider telemetry fallback'),
    ]
    result = {}
    for query, timestamp, module, success, source in sources:
        for row in query.order_by().annotate(day=TruncDate(timestamp, tzinfo=utc.utc)).values('user_id', module, 'day').annotate(count=Count('pk'), successful=Count('pk', filter=success)):
            application = row[module] or 'unspecified'
            key = (row['user_id'], application.replace('-', '_'), row['day'])
            if key in result and result[key]['source'] == source:
                result[key]['count'] += row['count']
                result[key]['successful'] += row['successful']
            elif key not in result:
                result[key] = {'user_id': row['user_id'], 'application': application, 'day': row['day'],
                               'count': row['count'], 'successful': row['successful'], 'source': source}
    return list(result.values())
