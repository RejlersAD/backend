"""Versioned enterprise technical proposal APIs."""
import hashlib

from django.db import transaction
from django.db.models import Max
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.serializers import ValidationError
from rest_framework.throttling import ScopedRateThrottle

from .access import accessible_projects, can_write_project
from .models import PlanningProject, ProposalExportRecord, ScheduleVersion, TechnicalProposal
from .proposal_serializers import ProposalExportRecordSerializer, TechnicalProposalSerializer
from .services.audit import record_event
from .services.proposal_builder import build_default_sections
from .services.proposal_exports import generate_proposal_export


class TechnicalProposalViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = TechnicalProposalSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'planning_exports'
    queryset = TechnicalProposal.objects.filter(is_deleted=False).select_related(
        'project', 'schedule_version__schedule', 'source_generation',
        'created_by', 'checked_by', 'approved_by',
    )

    def get_queryset(self):
        queryset = self.queryset.filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        project = PlanningProject.objects.filter(
            pk=request.data.get('project'), is_deleted=False,
        ).first()
        if not project or not accessible_projects(request.user).filter(pk=project.pk).exists():
            raise ValidationError({'project': 'Planning project was not found.'})
        if not can_write_project(request.user, project):
            raise PermissionDenied('You cannot create proposals for this project.')
        version_id = request.data.get('schedule_version')
        versions = ScheduleVersion.objects.filter(
            schedule__project=project, is_deleted=False, schedule__is_deleted=False,
        ).select_related('schedule', 'source_generation')
        version = versions.filter(pk=version_id).first() if version_id else versions.order_by('-version').first()
        if not version:
            raise ValidationError({'schedule_version': 'Generate or materialize a schedule before creating a proposal.'})
        generation = version.source_generation or project.generations.filter(is_deleted=False).first()
        snapshot, sections = build_default_sections(project, version, generation)
        # Serialize revision allocation per project so concurrent users cannot
        # allocate the same controlled revision.
        PlanningProject.objects.select_for_update().get(pk=project.pk)
        next_revision = (TechnicalProposal.objects.filter(project=project).aggregate(value=Max('revision'))['value'] or 0) + 1
        proposal = TechnicalProposal.objects.create(
            project=project, schedule_version=version, source_generation=generation,
            proposal_number=f'PROP-{project.id:04d}-{timezone.localdate().year}-{next_revision:02d}',
            revision=next_revision,
            title=request.data.get('title') or f'Technical Proposal – {project.name}',
            client_name=request.data.get('client_name') or project.client,
            opportunity_reference=request.data.get('opportunity_reference') or '',
            client_reference=request.data.get('client_reference') or '',
            tender_title=request.data.get('tender_title') or project.name,
            submission_date=request.data.get('submission_date') or None,
            validity_date=request.data.get('validity_date') or None,
            validity_days=request.data.get('validity_days') or 120,
            bid_focal_point=request.data.get('bid_focal_point') or {},
            submission_address=request.data.get('submission_address') or {},
            signatory=request.data.get('signatory') or {},
            sections=sections, snapshot=snapshot,
            branding={'company_name': 'Rejlers Abu Dhabi', 'confidentiality': 'CONFIDENTIAL'},
            created_by=request.user,
        )
        record_event(project=project, actor=request.user, action='proposal.created', entity=proposal, after={'revision': next_revision, 'schedule_version_id': version.id})
        return Response(self.get_serializer(proposal).data, status=status.HTTP_201_CREATED)

    def perform_update(self, serializer):
        proposal = serializer.instance
        if not can_write_project(self.request.user, proposal.project):
            raise PermissionDenied('You cannot edit this proposal.')
        if proposal.status not in ('draft', 'internal_review'):
            raise ValidationError('Approved or issued proposals are immutable; create a new revision.')
        before = {'title': proposal.title, 'sections': proposal.sections, 'status': proposal.status}
        proposal = serializer.save()
        record_event(project=proposal.project, actor=self.request.user, action='proposal.updated', entity=proposal, before=before, after={'title': proposal.title, 'sections': proposal.sections})

    def perform_destroy(self, instance):
        if instance.status == 'issued':
            raise ValidationError('Issued proposals cannot be archived.')
        if not can_write_project(self.request.user, instance.project):
            raise PermissionDenied('You cannot archive this proposal.')
        instance.soft_delete()
        record_event(project=instance.project, actor=self.request.user, action='proposal.archived', entity=instance)

    @action(detail=True, methods=['post'], url_path='transition')
    def transition(self, request, pk=None):
        proposal = self.get_object()
        if not can_write_project(request.user, proposal.project):
            raise PermissionDenied('You cannot transition this proposal.')
        target = request.data.get('status')
        allowed = {
            'draft': {'internal_review'}, 'internal_review': {'draft', 'approved'},
            'approved': {'draft', 'issued'}, 'issued': {'superseded'}, 'superseded': set(),
        }
        if target not in allowed.get(proposal.status, set()):
            raise ValidationError({'status': f'Cannot transition from {proposal.status} to {target}.'})
        previous = proposal.status
        proposal.status = target
        fields = ['status', 'updated_at']
        if target == 'internal_review': proposal.checked_by = request.user; fields.append('checked_by')
        if target == 'approved': proposal.approved_by = request.user; fields.append('approved_by')
        if target == 'issued': proposal.issued_at = timezone.now(); fields.append('issued_at')
        proposal.save(update_fields=fields)
        record_event(project=proposal.project, actor=request.user, action='proposal.transitioned', entity=proposal, before={'status': previous}, after={'status': target})
        return Response(self.get_serializer(proposal).data)

    @action(detail=True, methods=['post'], url_path='refresh')
    def refresh(self, request, pk=None):
        proposal = self.get_object()
        if proposal.status not in ('draft', 'internal_review'):
            raise ValidationError('Only draft or review proposals can refresh their source snapshot.')
        if not can_write_project(request.user, proposal.project):
            raise PermissionDenied('You cannot refresh this proposal.')
        version = proposal.schedule_version
        snapshot, generated_sections = build_default_sections(proposal.project, version, proposal.source_generation)
        edited = {section.get('key'): section for section in proposal.sections}
        for section in generated_sections:
            prior = edited.get(section['key'])
            if prior:
                section['content'] = prior.get('content', section['content'])
                section['included'] = prior.get('included', True)
        proposal.snapshot = snapshot
        proposal.sections = generated_sections
        proposal.save(update_fields=['snapshot', 'sections', 'updated_at'])
        record_event(project=proposal.project, actor=request.user, action='proposal.refreshed', entity=proposal, after={'schedule_version_id': version.id})
        return Response(self.get_serializer(proposal).data)

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        proposal = self.get_object()
        export_format = request.query_params.get('export_format', 'pdf').lower()
        try:
            content, content_type, filename = generate_proposal_export(proposal, export_format)
        except ValueError as exc:
            raise ValidationError({'export_format': str(exc)}) from exc
        digest = hashlib.sha256(content).hexdigest()
        ProposalExportRecord.objects.create(
            proposal=proposal, export_format=export_format, filename=filename,
            size_bytes=len(content), sha256=digest, requested_by=request.user,
        )
        record_event(project=proposal.project, actor=request.user, action='proposal.exported', entity=proposal, after={'format': export_format, 'filename': filename, 'sha256': digest})
        response = HttpResponse(content, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        response['X-Content-SHA256'] = digest
        return response


class ProposalExportRecordViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = ProposalExportRecordSerializer
    queryset = ProposalExportRecord.objects.filter(is_deleted=False).select_related('proposal__project', 'requested_by')

    def get_queryset(self):
        queryset = self.queryset.filter(proposal__project__in=accessible_projects(self.request.user))
        proposal_id = self.request.query_params.get('proposal')
        return queryset.filter(proposal_id=proposal_id) if proposal_id else queryset
