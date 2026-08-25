from rest_framework import serializers

from .models import ProposalExportRecord, TechnicalProposal


class TechnicalProposalSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    checked_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TechnicalProposal
        fields = [
            'id', 'project', 'schedule_version', 'source_generation', 'proposal_number',
            'revision', 'title', 'client_name', 'opportunity_reference', 'validity_date',
            'client_reference', 'tender_title', 'submission_date', 'validity_days',
            'bid_focal_point', 'submission_address', 'signatory',
            'status', 'sections', 'branding', 'snapshot', 'created_by', 'created_by_name',
            'checked_by', 'checked_by_name', 'approved_by', 'approved_by_name',
            'issued_at', 'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'project', 'schedule_version', 'source_generation', 'proposal_number',
            'revision', 'status', 'snapshot', 'created_by', 'checked_by', 'approved_by',
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


class ProposalExportRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProposalExportRecord
        fields = '__all__'
        read_only_fields = fields
