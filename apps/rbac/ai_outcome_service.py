from django.db import transaction, IntegrityError
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError, PermissionDenied, NotFound
from .ai_outcome_models import AIOutcomeEvidence
from .models import AuditLog, Module
from .ai_workforce_service import DEFAULT_MODULES
from django.conf import settings
from decimal import Decimal
from .ai_measurement_models import AIWorkflowRun


class OutcomeInput(serializers.Serializer):
    module = serializers.SlugRelatedField(slug_field='code', queryset=Module.objects.filter(is_active=True))
    title = serializers.CharField(max_length=200)
    task_reference = serializers.CharField(max_length=200)
    evidence_url = serializers.URLField(max_length=1000)
    comparison = serializers.CharField(min_length=20, max_length=4000)
    baseline_minutes = serializers.IntegerField(min_value=1, max_value=525600)
    ai_minutes = serializers.IntegerField(min_value=0, max_value=525600)
    review_minutes = serializers.IntegerField(min_value=0, max_value=525600)
    rework_minutes = serializers.IntegerField(min_value=0, max_value=525600)
    measurement = serializers.ChoiceField(choices=['measured', 'self_reported'])
    workflow = serializers.UUIDField(required=False, allow_null=True)
    contribution_type = serializers.ChoiceField(choices=['task', 'workflow_integration', 'automation_creator'], default='task')
    hourly_rate = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0'), required=False, allow_null=True)
    value_currency = serializers.ChoiceField(choices=['USD', 'AED', 'EUR', 'GBP', 'SEK', ''], required=False, default='')

    def validate(self, values):
        if values.get('hourly_rate') is not None and not values.get('value_currency'):
            raise ValidationError('Choose a currency for the optional hourly rate.')
        if values.get('hourly_rate') is None and values.get('value_currency'):
            raise ValidationError('Supply an hourly rate when choosing a currency.')
        if values['contribution_type'] != 'task' and not values.get('workflow'):
            raise ValidationError('Workflow integration and automation evidence must link to a completed AI workflow.')
        return values

    def validate_evidence_url(self, value):
        if not value.startswith('https://'):
            raise ValidationError('Use an HTTPS evidence link.')
        return value

    def validate_module(self, value):
        if value.code not in getattr(settings, 'AI_ADOPTION_MODULE_APPLICATIONS', DEFAULT_MODULES):
            raise ValidationError('Choose a configured AI module.')
        return value


class OutcomeReview(serializers.Serializer):
    id = serializers.UUIDField()
    decision = serializers.ChoiceField(choices=['approved', 'rejected'])
    reason = serializers.CharField(min_length=10, max_length=2000)
    comparable_quality_confirmed = serializers.BooleanField()


def serialize(row, reviewer_id):
    return {'id': str(row.pk), 'title': row.title, 'task_reference': row.task_reference,
            'workflow': str(row.workflow_id) if row.workflow_id else None,
            'contribution_type': row.contribution_type, 'hourly_rate': row.hourly_rate, 'value_currency': row.value_currency,
            'module': row.module.name, 'submitted_by': row.submitted_by.get_full_name() or row.submitted_by.email,
            'created_at': row.created_at, 'status': row.status, 'measurement': row.measurement,
            'baseline_minutes': row.baseline_minutes, 'ai_minutes': row.ai_minutes,
            'review_minutes': row.review_minutes, 'rework_minutes': row.rework_minutes,
            'saved_minutes': row.saved_minutes, 'comparison': row.comparison, 'evidence_url': row.evidence_url,
            'review_reason': row.review_reason, 'reviewed_at': row.reviewed_at,
            'can_review': row.status == 'pending' and row.submitted_by_id != reviewer_id}


def audit(row, user, action, changes):
    AuditLog.objects.create(user=user, user_email=user.email, action=action,
                            resource_type='AIOutcomeEvidence', resource_id=row.pk,
                            resource_repr=row.title, changes=changes,
                            metadata={'organization_id': str(row.organization_id)}, success=True)


def submit(data, user, can_cross_org=False):
    form = OutcomeInput(data=data)
    form.is_valid(raise_exception=True)
    profile = getattr(user, 'rbac_profile', None)
    if not profile or profile.is_deleted or not profile.organization_id:
        raise PermissionDenied('An active organization profile is required.')
    values = form.validated_data
    organization_id = profile.organization_id
    workflow_id = values.pop('workflow', None)
    if workflow_id:
        workflows = AIWorkflowRun.objects.filter(pk=workflow_id, module=values['module'].code,
                    status='completed', requests__provenance='server', requests__success=True)
        if not can_cross_org:
            workflows = workflows.filter(organization_id=organization_id)
        workflow = workflows.first()
        if not workflow or not workflow.source_id:
            raise ValidationError('Select a completed AI workflow with a persisted output in your organization and module.')
        if AIOutcomeEvidence.objects.filter(workflow=workflow).exists():
            raise ValidationError('This workflow already has outcome evidence.')
        values['workflow'] = workflow
        organization_id = workflow.organization_id
        values['task_reference'] = f'{workflow.source_type}:{workflow.source_id}'
    try:
        with transaction.atomic():
            row = AIOutcomeEvidence.objects.create(submitted_by=user, organization_id=organization_id, **values)
            audit(row, user, 'create', {'status': {'old': None, 'new': 'pending'}})
    except IntegrityError:
        if AIOutcomeEvidence.objects.filter(organization_id=organization_id, module=values['module'], task_reference=values['task_reference']).exists():
            raise ValidationError('Evidence for this task already exists; duplicate outcomes are not counted.')
        raise
    return serialize(row, user.pk)


def review(data, user, queryset):
    form = OutcomeReview(data=data)
    form.is_valid(raise_exception=True)
    values = form.validated_data
    with transaction.atomic():
        row = queryset.select_for_update().filter(pk=values['id']).first()
        if row is None:
            raise NotFound('Outcome not found.')
        if row.submitted_by_id == user.pk:
            raise PermissionDenied('Another administrator must review your evidence.')
        if row.workflow_id and row.workflow.user_id == user.pk:
            raise PermissionDenied('The workflow operator cannot approve their own outcome.')
        if row.status != 'pending':
            raise ValidationError('This outcome has already been reviewed.')
        if values['decision'] == 'approved' and not values['comparable_quality_confirmed']:
            raise ValidationError('Confirm comparable task scope and accepted output quality before approving.')
        row.status = values['decision']
        row.review_reason = values['reason']
        row.reviewed_by = user
        row.reviewed_at = timezone.now()
        row.save(update_fields=['status', 'review_reason', 'reviewed_by', 'reviewed_at'])
        audit(row, user, 'update', {'status': {'old': 'pending', 'new': row.status}, 'reason': row.review_reason})
    return serialize(row, user.pk)
