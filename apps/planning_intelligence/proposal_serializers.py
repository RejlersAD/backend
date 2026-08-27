from rest_framework import serializers

from .access import can_approve_proposal, can_write_project
from .models import ProposalExportRecord, ProposalWorkflowTask, TechnicalProposal


class ProposalWorkflowTaskSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField()
    assigned_by_name = serializers.SerializerMethodField()
    proposal_number = serializers.CharField(source='proposal.proposal_number', read_only=True)
    proposal_revision = serializers.IntegerField(source='proposal.revision', read_only=True)
    project = serializers.IntegerField(source='proposal.project_id', read_only=True)
    project_name = serializers.CharField(source='proposal.project.name', read_only=True)

    class Meta:
        model = ProposalWorkflowTask
        fields = [
            'id', 'proposal', 'proposal_number', 'proposal_revision', 'project', 'project_name',
            'task_type', 'status', 'assigned_to', 'assigned_to_name',
            'assigned_by', 'assigned_by_name', 'due_date', 'comments',
            'completed_at', 'created_at', 'updated_at',
        ]

    @staticmethod
    def _name(user):
        return (user.get_full_name() or user.email or user.username) if user else None

    def get_assigned_to_name(self, obj):
        return self._name(obj.assigned_to)

    def get_assigned_by_name(self, obj):
        return self._name(obj.assigned_by)


class ProposalExportRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalExportRecord
        fields = '__all__'
        read_only_fields = tuple(field.name for field in ProposalExportRecord._meta.fields)


class TechnicalProposalSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    checked_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    reviewer_name = serializers.SerializerMethodField()
    approver_name = serializers.SerializerMethodField()
    workflow_tasks = ProposalWorkflowTaskSerializer(many=True, read_only=True)
    issued_files = serializers.SerializerMethodField()
    workflow_permissions = serializers.SerializerMethodField()

    class Meta:
        model = TechnicalProposal
        fields = [
            'id', 'project', 'schedule_version', 'source_generation', 'proposal_number',
            'revision', 'title', 'client_name', 'opportunity_reference', 'validity_date',
            'client_reference', 'tender_title', 'submission_date', 'validity_days',
            'bid_focal_point', 'submission_address', 'signatory',
            'status', 'sections', 'branding', 'snapshot', 'created_by', 'created_by_name',
            'checked_by', 'checked_by_name', 'approved_by', 'approved_by_name',
            'reviewer', 'reviewer_name', 'approver', 'approver_name',
            'review_due_date', 'approval_due_date', 'review_submitted_at',
            'review_completed_at', 'approval_submitted_at', 'approved_at',
            'rejected_at', 'review_comments', 'approval_comments',
            'workflow_tasks', 'issued_files', 'workflow_permissions',
            'issued_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'project', 'schedule_version', 'source_generation', 'proposal_number',
            'revision', 'status', 'snapshot', 'created_by', 'checked_by', 'approved_by',
            'reviewer', 'approver', 'review_due_date', 'approval_due_date',
            'review_submitted_at', 'review_completed_at', 'approval_submitted_at',
            'approved_at', 'rejected_at', 'review_comments', 'approval_comments',
            'issued_at', 'created_at', 'updated_at',
        ]

    @staticmethod
    def _name(user):
        if not user:
            return None
        return user.get_full_name() or user.email or user.username

    def get_created_by_name(self, obj):
        return self._name(obj.created_by)

    def get_checked_by_name(self, obj):
        return self._name(obj.checked_by)

    def get_approved_by_name(self, obj):
        return self._name(obj.approved_by)

    def get_reviewer_name(self, obj):
        return self._name(obj.reviewer)

    def get_approver_name(self, obj):
        return self._name(obj.approver)

    def get_issued_files(self, obj):
        rows = obj.export_records.filter(is_deleted=False, is_issued_artifact=True)
        return ProposalExportRecordSerializer(rows, many=True, context=self.context).data

    def get_workflow_permissions(self, obj):
        request = self.context.get('request')
        user = getattr(request, 'user', None)
        user_id = getattr(user, 'id', None)
        approval_authority = can_approve_proposal(user, obj.project) if user_id else False
        write_access = can_write_project(user, obj.project) if user_id else False
        return {
            'can_edit': obj.status == 'draft' and write_access,
            'can_submit_review': obj.status == 'draft' and write_access,
            'can_review': obj.status == 'internal_review' and obj.reviewer_id == user_id,
            'can_reassign_reviewer': obj.status == 'internal_review' and write_access,
            'can_reassign_approver': obj.status == 'approval_review' and (
                write_access or obj.checked_by_id == user_id
            ),
            'can_approve': (
                obj.status == 'approval_review' and obj.approver_id == user_id
                and approval_authority and obj.created_by_id != user_id and obj.checked_by_id != user_id
            ),
            'can_issue': obj.status == 'approved' and approval_authority,
            'can_reopen': obj.status == 'rejected' and write_access,
            'can_supersede': obj.status == 'issued' and approval_authority,
        }

    def validate_sections(self, value):
        if not isinstance(value, list) or not value:
            raise serializers.ValidationError('At least one proposal section is required.')
        keys = set()
        cleaned = []
        for index, section in enumerate(value):
            if not isinstance(section, dict):
                raise serializers.ValidationError(f'Section {index + 1} must be an object.')
            key = str(section.get('key') or '').strip()
            title = str(section.get('title') or '').strip()
            if not key or not title or key in keys:
                raise serializers.ValidationError('Every section requires a unique key and title.')
            keys.add(key)
            cleaned.append({
                'key': key[:64], 'title': title[:255],
                'content': str(section.get('content') or ''),
                'included': bool(section.get('included', True)),
                'data': section.get('data') if isinstance(section.get('data'), list) else [],
                'number': str(section.get('number') or '')[:24],
                'group': str(section.get('group') or 'Technical Proposal')[:80],
                'section_type': str(section.get('section_type') or 'narrative')[:32],
                'source': str(section.get('source') or 'planner')[:32],
                'required': bool(section.get('required', False)),
                'readiness': str(section.get('readiness') or 'draft')[:24],
            })
        return cleaned
