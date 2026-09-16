from django.contrib.auth import get_user_model
from django.db.models import Q
from rest_framework import serializers

from apps.core.project_models import ProjectMilestone
from apps.planning_intelligence.models import ScheduleActivity
from apps.procurement.models import PurchaseOrder
from .access import accessible_enterprise_projects, can_write_enterprise_project
from .epc_models import IntegratedBaseline
from .execution_models import EPCWorkEvent, EPCWorkItem
from .models import ProjectDocument, WBSNode
from .services.execution import action_blockers, can_accept_work, execution_scope_blockers, material_readiness


def project_people(project):
    return get_user_model().objects.filter(is_active=True).filter(
        Q(pk=project.owner_id) | Q(projectmember__project=project, projectmember__is_active=True)
        | Q(is_staff=True) | Q(is_superuser=True)).distinct().order_by('first_name', 'id')


def person_name(user):
    return user.get_full_name() or user.username


class WorkEventSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    def get_actor_name(self, obj):
        return person_name(obj.actor)

    class Meta:
        model = EPCWorkEvent
        fields = ['id', 'action', 'actor', 'actor_name', 'note', 'payload', 'created_at']


class EPCWorkItemSerializer(serializers.ModelSerializer):
    acceptance_criteria = serializers.ListField(child=serializers.CharField(max_length=500, allow_blank=False), min_length=1, max_length=50)
    owner_name = serializers.SerializerMethodField()
    reviewer_name = serializers.SerializerMethodField()
    wbs_code = serializers.CharField(source='wbs_node.code', read_only=True)
    activity_name = serializers.CharField(source='activity.name', default='', read_only=True)
    document_details = serializers.SerializerMethodField()
    predecessor_details = serializers.SerializerMethodField()
    action_blockers = serializers.SerializerMethodField()
    material_readiness = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_submit = serializers.SerializerMethodField()
    can_review = serializers.SerializerMethodField()
    can_accept = serializers.SerializerMethodField()
    events = WorkEventSerializer(many=True, read_only=True)

    class Meta:
        model = EPCWorkItem
        fields = ['id', 'project', 'code', 'title', 'phase', 'wbs_node', 'owner', 'reviewer', 'activity',
            'baseline', 'documents', 'predecessors', 'purchase_order', 'requires_materials', 'milestone',
            'acceptance_criteria', 'evidence_note', 'data_date', 'status', 'owner_name', 'reviewer_name',
            'wbs_code', 'activity_name', 'document_details', 'predecessor_details', 'action_blockers',
            'material_readiness', 'can_edit', 'can_submit', 'can_review', 'can_accept', 'events',
            'submitted_by', 'submitted_at', 'reviewed_at', 'review_note', 'accepted_by', 'accepted_at',
            'acceptance_manifest', 'progress_update', 'control_snapshot', 'created_at', 'updated_at']
        read_only_fields = ['status', 'submitted_by', 'submitted_at', 'reviewed_at', 'review_note',
            'accepted_by', 'accepted_at', 'acceptance_manifest', 'progress_update', 'control_snapshot',
            'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request:
            projects = accessible_enterprise_projects(request.user)
            self.fields['project'].queryset = projects
            self.fields['wbs_node'].queryset = WBSNode.objects.filter(project__in=projects, is_deleted=False)
            self.fields['activity'].queryset = ScheduleActivity.objects.filter(is_deleted=False,
                version__schedule__project__enterprise_project__in=projects)
            self.fields['baseline'].queryset = IntegratedBaseline.objects.filter(project__in=projects)
            self.fields['purchase_order'].queryset = PurchaseOrder.objects.filter(enterprise_project__in=projects)
            self.fields['milestone'].queryset = ProjectMilestone.objects.filter(project__in=projects, is_deleted=False)
            self.fields['documents'].child_relation.queryset = ProjectDocument.objects.filter(project__in=projects, is_deleted=False)
            self.fields['predecessors'].child_relation.queryset = EPCWorkItem.objects.filter(project__in=projects, is_deleted=False)
            self.fields['owner'].queryset = get_user_model().objects.filter(is_active=True)
            self.fields['reviewer'].queryset = get_user_model().objects.filter(is_active=True)

    def validate(self, attrs):
        def current(key, default=None):
            return attrs.get(key, getattr(self.instance, key, default))
        project = current('project')
        if self.instance and self.instance.status != 'draft':
            raise serializers.ValidationError('Submitted, reviewed and accepted work is read-only. The reviewer can return unaccepted work.')
        if self.instance and project.pk != self.instance.project_id:
            raise serializers.ValidationError({'project': 'A work item cannot move to another project.'})
        request = self.context.get('request')
        if request and not can_write_enterprise_project(request.user, project):
            raise serializers.ValidationError({'project': 'Project write access is required.'})
        for key in ('wbs_node', 'baseline', 'milestone'):
            related = current(key)
            if related and (related.project_id != project.pk or getattr(related, 'is_deleted', False)):
                raise serializers.ValidationError({key: 'Select an active record from this project.'})
        scope_blockers = execution_scope_blockers(project, current('phase'), current('wbs_node'))
        if scope_blockers:
            raise serializers.ValidationError({'phase': scope_blockers})
        activity = current('activity')
        if activity and (activity.version.schedule.project.enterprise_project_id != project.pk or
                        activity.version.is_deleted or activity.version.status == 'superseded'):
            raise serializers.ValidationError({'activity': 'Select an activity from an active version linked to this project.'})
        po = current('purchase_order')
        if po and po.enterprise_project_id != project.pk:
            raise serializers.ValidationError({'purchase_order': 'The order must be explicitly assigned to this project.'})
        people = set(project_people(project).values_list('pk', flat=True))
        for key in ('owner', 'reviewer'):
            person = current(key)
            if person.pk not in people:
                raise serializers.ValidationError({key: 'Select an active project member, owner or administrator.'})
        if current('owner').pk == current('reviewer').pk:
            raise serializers.ValidationError({'reviewer': 'Assign a reviewer other than the work owner.'})
        for key in ('documents', 'predecessors'):
            for related in attrs.get(key, []):
                if related.project_id != project.pk or related.is_deleted:
                    raise serializers.ValidationError({key: 'All linked records must belong to this project.'})
        if self.instance and 'predecessors' in attrs:
            visited = set()
            pending = list(attrs['predecessors'])
            while pending:
                predecessor = pending.pop()
                if predecessor.pk == self.instance.pk:
                    raise serializers.ValidationError({'predecessors': 'Predecessor links cannot create a cycle.'})
                if predecessor.pk not in visited:
                    visited.add(predecessor.pk)
                    pending.extend(predecessor.predecessors.all())
        return attrs

    def get_owner_name(self, obj):
        return person_name(obj.owner)

    def get_reviewer_name(self, obj):
        return person_name(obj.reviewer)

    def get_document_details(self, obj):
        return list(obj.documents.values('id', 'title', 'original_filename', 'is_deleted'))

    def get_predecessor_details(self, obj):
        return list(obj.predecessors.values('id', 'code', 'title', 'status'))

    def get_action_blockers(self, obj):
        cached = self.context.setdefault('_work_blockers', {})
        if obj.pk not in cached:
            cached[obj.pk] = action_blockers(obj)
        return cached[obj.pk]

    def get_material_readiness(self, obj):
        return material_readiness(obj)

    def get_can_edit(self, obj):
        request = self.context.get('request')
        return bool(request and request.user.is_active and obj.status == 'draft' and can_write_enterprise_project(request.user, obj.project))

    def get_can_submit(self, obj):
        request = self.context.get('request')
        return bool(self.get_can_edit(obj) and request.user.pk != obj.reviewer_id and not self.get_action_blockers(obj)['submit'])

    def get_can_review(self, obj):
        request = self.context.get('request')
        from apps.rbac.approval_eligibility import approval_access
        return bool(request and request.user.is_active and request.user.pk == obj.reviewer_id
                    and approval_access(request.user, 'project_control')
                    and obj.status in ('submitted', 'reviewed'))

    def get_can_accept(self, obj):
        request = self.context.get('request')
        return bool(request and can_accept_work(request.user, obj.project) and not self.get_action_blockers(obj)['accept'])


class EPCReviewSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=['approve', 'return'])
    note = serializers.CharField(max_length=4000)
    criteria_confirmed = serializers.BooleanField(default=False)


class EPCAcceptSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=4000)
