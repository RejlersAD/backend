"""Evidence-based narrative generation for probation performance reports."""

from datetime import datetime, time, timedelta

from django.db.models import Count, Q, Sum
from django.utils import timezone

from apps.hr_core.models import EmployeeMaster
from apps.rbac.ai_champion_models import AIUsageLog, ActivityEvent
from apps.rbac.models import EngineerProfile, UserProfile as RBACUserProfile
from apps.timesheet.models import DailyAttendanceSummary

from .project_assignments import get_active_project_assignments


def _period_bounds(join_date, checkpoint_days=100):
    end_date = min(join_date + timedelta(days=checkpoint_days), timezone.localdate())
    start = timezone.make_aware(datetime.combine(join_date, time.min))
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min))
    return end_date, start, end


def _employee_name(employee):
    """Resolve the employee's actual name without exposing an email fallback."""
    user = getattr(employee, 'user', None)
    candidates = [
        user.get_full_name() if user and hasattr(user, 'get_full_name') else '',
        employee.get_display_name() if hasattr(employee, 'get_display_name') else '',
        employee.get_full_name() if hasattr(employee, 'get_full_name') else '',
    ]
    for candidate in candidates:
        candidate = str(candidate or '').strip()
        if candidate and '@' not in candidate:
            return candidate

    from .models import OnboardingRecord

    onboarding_name = OnboardingRecord.objects.filter(user=user).order_by('-created_at').values_list(
        'employee_name', flat=True
    ).first() if user else ''
    if onboarding_name and '@' not in onboarding_name:
        return onboarding_name.strip()

    employee_number = str(getattr(employee, 'employee_number', '') or '').strip()
    return f"Employee {employee_number}" if employee_number else 'Employee'


def narratives_from_snapshot(employee, snapshot):
    """Turn trusted RADAI metrics into concise, factual report sections."""
    name = _employee_name(employee)
    activity = snapshot['activity']
    attendance = snapshot['attendance']
    projects = snapshot['projects']
    profile = snapshot['profile']

    activity_parts = []
    if activity['total_actions']:
        activity_parts.append(
            f"{activity['successful_actions']} of {activity['total_actions']} recorded RADAI activities were successful"
        )
    if activity['ai_requests']:
        activity_parts.append(f"completed {activity['ai_requests']} AI-assisted requests")
    if activity['features_used']:
        activity_parts.append(f"used {activity['features_used']} platform features")
    if projects:
        project_names = ', '.join(project['name'] for project in projects[:3])
        activity_parts.append(f"contributed to {len(projects)} active project(s): {project_names}")
    achievements = (
        f"During the review period, {name} " + '; '.join(activity_parts) + '.'
        if activity_parts
        else f"No measurable RADAI activity or active project assignment was recorded for {name} during this review period."
    )

    strength_parts = []
    if activity['total_actions'] and activity['success_rate'] >= 90:
        strength_parts.append(f"a {activity['success_rate']:.0f}% successful activity rate")
    if activity['features_used'] >= 3:
        strength_parts.append('broad use of RADAI tools')
    if attendance['days_present']:
        strength_parts.append(
            f"consistent recorded attendance across {attendance['days_present']} working day(s)"
        )
    if profile['skills']:
        strength_parts.append(f"documented capability in {', '.join(profile['skills'][:4])}")
    strengths = (
        'RADAI evidence highlights ' + ', '.join(strength_parts) + '.'
        if strength_parts
        else 'RADAI currently has limited evidence for a reliable strengths assessment; manager validation is recommended.'
    )

    improvement_parts = []
    if activity['total_actions'] == 0:
        improvement_parts.append('begin recording work through the relevant RADAI workflows')
    elif activity['features_used'] < 3:
        improvement_parts.append('broaden use beyond the currently recorded RADAI features')
    if activity['total_actions'] and activity['success_rate'] < 90:
        improvement_parts.append(f"improve workflow completion quality from the current {activity['success_rate']:.0f}% success rate")
    if attendance['open_shifts']:
        improvement_parts.append(f"resolve {attendance['open_shifts']} open attendance shift(s)")
    if not projects:
        improvement_parts.append('confirm and record an active project assignment')
    improvement_areas = (
        'System-indicated focus areas: ' + '; '.join(improvement_parts) + '.'
        if improvement_parts
        else 'No material exception is visible in the available RADAI records; continue maintaining consistent delivery and complete records.'
    )

    goal_parts = ['complete the remaining probation objectives with the line manager']
    if activity['features_used'] < 3:
        goal_parts.append('use at least three relevant RADAI capabilities in regular delivery')
    else:
        goal_parts.append('maintain effective and responsible use of the current RADAI toolset')
    if projects:
        goal_parts.append('document progress against assigned project deliverables')
    else:
        goal_parts.append('record the confirmed project or operational assignment')
    next_period_goals = 'Recommended next-period goals: ' + '; '.join(goal_parts) + '.'

    evidence = [
        f"{activity['total_actions']} activities",
        f"{activity['ai_requests']} AI requests",
        f"{activity['features_used']} features",
        f"{len(projects)} active projects",
        f"{attendance['days_present']} attendance days",
    ]
    overall_comments = (
        f"RADAI-generated 100-day review for {name}, based on " + ', '.join(evidence) + '. '
        'The ratings and final employment assessment remain the responsibility of HR and the direct line manager.'
    )
    return {
        'achievements': achievements,
        'strengths': strengths,
        'improvement_areas': improvement_areas,
        'next_period_goals': next_period_goals,
        'overall_comments': overall_comments,
    }


def build_probation_report_insights(employee_user, checkpoint_days=100):
    """Collect first-party RADAI signals and return persisted report values."""
    employee = EmployeeMaster.objects.get(user=employee_user)
    end_date, start_at, end_at = _period_bounds(employee.join_date, checkpoint_days)

    activity = ActivityEvent.objects.filter(
        user=employee_user, timestamp__gte=start_at, timestamp__lt=end_at
    ).aggregate(
        total_actions=Count('id'),
        successful_actions=Count('id', filter=Q(success=True)),
        features_used=Count('feature', distinct=True),
    )
    total_actions = activity['total_actions'] or 0
    successful_actions = activity['successful_actions'] or 0
    ai_requests = AIUsageLog.objects.filter(
        user=employee_user, timestamp__gte=start_at, timestamp__lt=end_at
    ).count()

    codes = [code for code in (employee.employee_code, employee.emp_code, employee.employee_number) if code]
    attendance = DailyAttendanceSummary.objects.filter(
        employee_code__in=codes,
        date__gte=employee.join_date,
        date__lte=end_date,
    ).aggregate(
        days_present=Count('date', filter=Q(effective_hours__gt=0), distinct=True),
        total_hours=Sum('effective_hours'),
        overtime_hours=Sum('overtime_hours'),
        open_shifts=Count('id', filter=Q(open_shift=True)),
    )

    project_rows = get_active_project_assignments(employee_user)
    projects = [
        {'code': row['code'], 'name': row['name'], 'source': row['source']}
        for row in project_rows
    ]

    profile = RBACUserProfile.objects.filter(user=employee_user, is_deleted=False).first()
    skills = []
    disciplines = []
    if profile:
        try:
            engineer_profile = profile.engineer_profile
            disciplines = engineer_profile.engineering_disciplines or []
            skills = [
                skill.get('name') if isinstance(skill, dict) else str(skill)
                for skill in (engineer_profile.technical_skills or [])
            ]
            skills = [skill for skill in skills if skill]
        except EngineerProfile.DoesNotExist:
            pass

    snapshot = {
        'period': {'start': str(employee.join_date), 'end': str(end_date), 'checkpoint_days': checkpoint_days},
        'activity': {
            'total_actions': total_actions,
            'successful_actions': successful_actions,
            'success_rate': round(successful_actions / total_actions * 100, 2) if total_actions else 0,
            'ai_requests': ai_requests,
            'features_used': activity['features_used'] or 0,
        },
        'attendance': {
            'days_present': attendance['days_present'] or 0,
            'total_hours': round(attendance['total_hours'] or 0, 2),
            'overtime_hours': round(attendance['overtime_hours'] or 0, 2),
            'open_shifts': attendance['open_shifts'] or 0,
        },
        'projects': projects,
        'profile': {'disciplines': disciplines, 'skills': skills},
    }
    return {
        **narratives_from_snapshot(employee, snapshot),
        'system_snapshot': snapshot,
        'insights_generated_at': timezone.now(),
    }
