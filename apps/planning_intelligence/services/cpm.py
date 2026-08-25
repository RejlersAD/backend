"""Calendar-aware critical path calculation for relational schedule versions."""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict, deque
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from ..models import Schedule, ScheduleCalculationRun, ScheduleVersion


class SchedulingError(Exception):
    """A schedule cannot be calculated because its network is invalid."""

    def __init__(self, message, *, code='invalid_schedule', issues=None):
        super().__init__(message)
        self.code = code
        self.issues = issues or []


class WorkdayCalendar:
    """Maps integer working-day offsets to dates using a stored work calendar."""

    def __init__(self, calendar, origin):
        weekdays = calendar.working_weekdays if calendar else [0, 1, 2, 3, 4]
        self.weekdays = {int(day) for day in weekdays if 0 <= int(day) <= 6}
        if not self.weekdays:
            raise SchedulingError('The schedule calendar has no working weekdays.', code='empty_calendar')
        self.exceptions = {
            item.date: item.is_working
            for item in (calendar.exceptions.filter(is_deleted=False) if calendar else [])
        }
        self.origin = self.on_or_after(origin)
        self._forward = {0: self.origin}
        self._reverse = {self.origin: 0}
        self._max_index = 0

    def is_working(self, value):
        return self.exceptions.get(value, value.weekday() in self.weekdays)

    def on_or_after(self, value):
        while not self.is_working(value):
            value += dt.timedelta(days=1)
        return value

    def on_or_before(self, value):
        while not self.is_working(value):
            value -= dt.timedelta(days=1)
        return value

    def date_at(self, index):
        if index < 0:
            value = self.origin
            remaining = -index
            while remaining:
                value -= dt.timedelta(days=1)
                if self.is_working(value):
                    remaining -= 1
            return value
        while self._max_index < index:
            value = self._forward[self._max_index] + dt.timedelta(days=1)
            value = self.on_or_after(value)
            self._max_index += 1
            self._forward[self._max_index] = value
            self._reverse[value] = self._max_index
        return self._forward[index]

    def index_of(self, value):
        value = self.on_or_after(value)
        if value < self.origin:
            index = 0
            cursor = self.origin
            while cursor > value:
                cursor -= dt.timedelta(days=1)
                if self.is_working(cursor):
                    index -= 1
            return index
        while self.date_at(self._max_index) < value:
            self.date_at(self._max_index + 1)
        return self._reverse[value]


@dataclass(frozen=True)
class NetworkActivity:
    model: object
    duration: int


def _duration(activity):
    if activity.is_milestone:
        return 0
    return max(0, math.ceil(float(activity.duration_days)))


def _edge_weight(kind, predecessor_duration, successor_duration, lag):
    lag = math.ceil(float(lag))
    return {
        'FS': predecessor_duration + lag,
        'SS': lag,
        'FF': predecessor_duration + lag - successor_duration,
        'SF': lag - successor_duration,
    }[kind]


def _finish_date(calendar, start_index, duration):
    return calendar.date_at(start_index if duration == 0 else start_index + duration - 1)


def _constraint_bound(calendar, activity, duration):
    if activity.constraint_type == 'none' or not activity.constraint_date:
        return None, None
    point = calendar.index_of(activity.constraint_date)
    if activity.constraint_type in ('finish_no_later', 'must_finish') and duration:
        point -= duration - 1
    if activity.constraint_type in ('start_no_earlier',):
        return point, None
    if activity.constraint_type in ('start_no_later', 'finish_no_later'):
        return None, point
    return point, point


def calculate_schedule_version(version, *, requested_by=None):
    """Run CPM, persist calculated dates/float, and return its durable run record."""
    run = ScheduleCalculationRun.objects.create(
        version=version, status='running', started_at=timezone.now(), requested_by=requested_by,
    )
    try:
        with transaction.atomic():
            version = (
                ScheduleVersion.objects.select_for_update()
                .get(pk=version.pk, is_deleted=False)
            )
            # PostgreSQL cannot apply FOR UPDATE to the nullable side of the
            # parent_version outer join introduced by select_related(). Lock
            # only the version row, then load its schedule/calendar normally.
            schedule = Schedule.objects.select_related('default_calendar').get(pk=version.schedule_id)
            activities = list(
                version.activities.filter(is_deleted=False)
                .select_related('calendar').order_by('sort_order', 'external_id')
            )
            if not activities:
                raise SchedulingError('The schedule version has no activities.', code='empty_schedule')

            calendar = WorkdayCalendar(schedule.default_calendar, schedule.planned_start)
            nodes = {activity.pk: NetworkActivity(activity, _duration(activity)) for activity in activities}
            incoming = defaultdict(list)
            outgoing = defaultdict(list)
            indegree = {pk: 0 for pk in nodes}
            issues = []

            relationships = list(version.relationships.filter(is_deleted=False))
            for relationship in relationships:
                if relationship.predecessor_id not in nodes or relationship.successor_id not in nodes:
                    issues.append({'code': 'relationship_outside_version', 'relationship_id': relationship.pk})
                    continue
                pred = nodes[relationship.predecessor_id]
                succ = nodes[relationship.successor_id]
                weight = _edge_weight(
                    relationship.relationship_type, pred.duration, succ.duration, relationship.lag_days,
                )
                incoming[relationship.successor_id].append((relationship.predecessor_id, weight))
                outgoing[relationship.predecessor_id].append((relationship.successor_id, weight))
                indegree[relationship.successor_id] += 1

            queue = deque(pk for pk, degree in indegree.items() if degree == 0)
            order = []
            while queue:
                pk = queue.popleft()
                order.append(pk)
                for successor_id, _ in outgoing[pk]:
                    indegree[successor_id] -= 1
                    if indegree[successor_id] == 0:
                        queue.append(successor_id)
            if len(order) != len(nodes):
                cyclic = [nodes[pk].model.external_id for pk, degree in indegree.items() if degree]
                raise SchedulingError(
                    'The activity network contains a dependency cycle.', code='dependency_cycle',
                    issues=[{'code': 'dependency_cycle', 'activities': cyclic}],
                )

            mixed = sorted({item.model.calendar_id for item in nodes.values() if item.model.calendar_id and item.model.calendar_id != schedule.default_calendar_id})
            if mixed:
                issues.append({'code': 'mixed_calendars_normalized', 'calendar_ids': mixed})
            starts = [nodes[pk].model.external_id for pk in order if not incoming[pk]]
            finishes = [nodes[pk].model.external_id for pk in order if not outgoing[pk]]
            if len(starts) > 1:
                issues.append({'code': 'multiple_open_starts', 'activities': starts})
            if len(finishes) > 1:
                issues.append({'code': 'multiple_open_finishes', 'activities': finishes})

            early = {}
            upper_bounds = {}
            for pk in order:
                item = nodes[pk]
                network_start = max((early[pred] + weight for pred, weight in incoming[pk]), default=0)
                lower, upper = _constraint_bound(calendar, item.model, item.duration)
                start = max(network_start, lower) if lower is not None else network_start
                if upper is not None:
                    upper_bounds[pk] = upper
                    if start > upper:
                        issues.append({
                            'code': 'constraint_violation', 'activity': item.model.external_id,
                            'constraint': item.model.constraint_type,
                            'variance_days': start - upper,
                        })
                early[pk] = start

            project_finish = max(early[pk] + nodes[pk].duration for pk in order)
            late = {pk: project_finish - nodes[pk].duration for pk in order}
            for pk, upper in upper_bounds.items():
                late[pk] = min(late[pk], upper)
            for pk in reversed(order):
                for successor_id, weight in outgoing[pk]:
                    late[pk] = min(late[pk], late[successor_id] - weight)

            updates = []
            critical_count = 0
            for pk in order:
                item = nodes[pk]
                activity = item.model
                total_float = late[pk] - early[pk]
                free_float = min(
                    (early[successor_id] - (early[pk] + weight) for successor_id, weight in outgoing[pk]),
                    default=project_finish - (early[pk] + item.duration),
                )
                activity.early_start = activity.planned_start = calendar.date_at(early[pk])
                activity.early_finish = activity.planned_finish = _finish_date(calendar, early[pk], item.duration)
                activity.late_start = calendar.date_at(late[pk])
                activity.late_finish = _finish_date(calendar, late[pk], item.duration)
                activity.total_float_days = total_float
                activity.free_float_days = free_float
                activity.is_critical = total_float <= 0
                critical_count += int(activity.is_critical)
                updates.append(activity)
            version.activities.bulk_update(updates, [
                'planned_start', 'planned_finish', 'early_start', 'early_finish',
                'late_start', 'late_finish', 'total_float_days', 'free_float_days',
                'is_critical', 'updated_at',
            ])

            finish_index = max(early[pk] + max(nodes[pk].duration - 1, 0) for pk in order)
            finish_date = calendar.date_at(finish_index)
            version.status = 'calculated'
            version.calculated_at = timezone.now()
            version.calculated_finish = finish_date
            version.save(update_fields=['status', 'calculated_at', 'calculated_finish', 'updated_at'])
            run.status = 'succeeded'
            run.finished_at = timezone.now()
            run.activity_count = len(activities)
            run.critical_activity_count = critical_count
            run.project_finish = finish_date
            run.issues = issues
            run.save(update_fields=[
                'status', 'finished_at', 'activity_count', 'critical_activity_count',
                'project_finish', 'issues', 'updated_at',
            ])
        return run
    except Exception as exc:
        run.status = 'failed'
        run.finished_at = timezone.now()
        run.error_message = str(exc)
        run.issues = getattr(exc, 'issues', [])
        run.save(update_fields=['status', 'finished_at', 'error_message', 'issues', 'updated_at'])
        raise
