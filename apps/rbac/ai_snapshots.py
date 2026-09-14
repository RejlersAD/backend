from datetime import timedelta, timezone as utc
from django.contrib.auth import get_user_model
from django.utils import timezone
from .ai_cohort import current_cohort
from .ai_measurement_models import AIWorkforceSnapshot
from .models import Organization


POLICY_VERSION = 'radai-linked-active-v2'


def capture_workforce():
    now = timezone.now()
    created = 0
    for org in Organization.objects.all().iterator():
        people, quality, _, _ = current_cohort(organization_id=org.pk)
        _, added = AIWorkforceSnapshot.objects.get_or_create(organization=org, capture_date=now.astimezone(utc.utc).date(),
            defaults={'policy_version': POLICY_VERSION, 'captured_at': now, 'people': {str(uid): person for uid, person in people.items()}, 'quality': quality})
        created += added
    return created


def cohort_at(at, user_ids=None, organization_id=None, current_data=None):
    current, quality, modules, mapping = current_data or current_cohort(user_ids, organization_id)
    snapshots = AIWorkforceSnapshot.objects.filter(policy_version=POLICY_VERSION, captured_at__lte=at, captured_at__gte=at - timedelta(hours=36)).order_by('organization_id', '-captured_at')
    if organization_id is not None:
        snapshots = snapshots.filter(organization_id=organization_id)
    allowed = {str(uid) for uid in user_ids} if user_ids is not None else None
    if allowed is not None:
        from .models import UserProfile
        snapshots = snapshots.filter(organization_id__in=UserProfile.objects.filter(user_id__in=user_ids).values('organization_id'))
    selected = {}
    for snapshot in snapshots:
        selected.setdefault(str(snapshot.organization_id), snapshot)
    cohort = {uid: person for uid, person in current.items() if person['organization_id'] not in selected}
    pk_field = get_user_model()._meta.pk
    for snapshot in selected.values():
        for uid, person in snapshot.people.items():
            if allowed is None or uid in allowed:
                cohort[pk_field.to_python(uid)] = person
    current_orgs = set(str(o) for o in Organization.objects.filter(**({'pk': organization_id} if organization_id else {})).values_list('pk', flat=True))
    if allowed is not None:
        current_orgs = {p['organization_id'] for p in current.values()} | set(selected)
    complete = bool(selected) and current_orgs <= set(selected)
    basis = 'snapshot' if complete else 'mixed' if selected else 'current_fallback'
    description = ('Dated workforce snapshots at or before the reporting boundary (maximum age 36 hours).'
                   if complete else 'Some organizations use dated snapshots; organizations without a fresh historical snapshot use current membership.'
                   if selected else 'No fresh snapshot exists before this period. Current eligibility and current department/manager assignments are used; this is not a historical workforce count.')
    return cohort, quality, modules, mapping, {'basis': basis, 'description': description,
        'snapshots': [{'captured_at': s.captured_at, 'policy': s.policy_version} for s in selected.values()]}
