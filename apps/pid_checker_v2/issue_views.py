"""Controlled P&ID issue and EPC progress-evidence intake endpoints."""
from django.db import transaction
from django.http import HttpResponse
from rest_framework.exceptions import NotFound
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.pid_checker_v2.models import PidControlledIssue
from apps.pid_checker_v2.services.controlled_issue import (
    _scope, controlled_issue, issue_payload, register_progress_evidence,
)
from apps.pid_checker_v2.services.technical_review import _locked_link


class PidControlledIssueView(APIView):
    permission_classes = [IsAuthenticated]
    business_approval_actions = {'PidControlledIssueView'}

    @property
    def permission_action(self):
        return 'read' if self.request.method in ('GET', 'HEAD') else 'approve'

    @transaction.atomic
    def get(self, request, extraction_id):
        link = _locked_link(extraction_id)
        _scope(request.user, link)
        issue = PidControlledIssue.objects.defer('source_bytes').filter(link=link).first()
        if issue is None:
            raise NotFound('Controlled issue not found.')
        return Response(issue_payload(issue))

    def post(self, request, extraction_id):
        issue, created = controlled_issue(
            extraction_id, user=request.user,
            revision_id=request.data.get('revision_id'),
            evidence_fingerprint=request.data.get('evidence_fingerprint'),
        )
        return Response(issue_payload(issue), status=201 if created else 200)


class PidControlledIssueDownloadView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'read'

    @transaction.atomic
    def get(self, request, extraction_id):
        link = _locked_link(extraction_id)
        _scope(request.user, link)
        issue = PidControlledIssue.objects.filter(link=link).first()
        if issue is None:
            raise NotFound('Controlled issue not found.')
        response = HttpResponse(bytes(issue.source_bytes), content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{issue.issue_number}.pdf"'
        return response


class PidIssueProgressEvidenceView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'update'

    def post(self, request, extraction_id):
        issue, created = register_progress_evidence(
            extraction_id, user=request.user, note=request.data.get('note'),
        )
        return Response(issue_payload(issue), status=201 if created else 200)
