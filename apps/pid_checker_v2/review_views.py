"""Technical-review commands for linked P&ID evidence."""
from django.db import transaction
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.pid_checker_v2.services.engineering_evidence import project_access
from apps.rbac.action_policy import module_action_allowed
from apps.pid_checker_v2.services.technical_review import (
    _locked_link, comment, decide, review_payload, submit, validate,
)
from rest_framework.exceptions import PermissionDenied


class PidEvidenceReviewView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'read'

    @transaction.atomic
    def get(self, request, extraction_id):
        link = _locked_link(extraction_id)
        if not project_access(request.user, link.project):
            raise PermissionDenied('Project access is required.')
        if not module_action_allowed(request.user, 'crs_documents', 'read'):
            raise PermissionDenied('CRS document read access is required.')
        return Response(review_payload(link))


class PidEvidenceValidateView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'update'

    def post(self, request, extraction_id):
        link = validate(extraction_id, user=request.user,
                        line_list_id=request.data.get('line_list_id'))
        return Response(review_payload(link))


class PidEvidenceSubmitView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'update'

    def post(self, request, extraction_id):
        return Response(review_payload(submit(extraction_id, user=request.user)))


class PidEvidenceCommentView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'approve'
    business_approval_actions = {'PidEvidenceCommentView'}

    def post(self, request, extraction_id):
        return Response(review_payload(comment(extraction_id, user=request.user,
                                               note=request.data.get('note'))))


class PidEvidenceDecisionView(APIView):
    permission_classes = [IsAuthenticated]
    permission_action = 'approve'
    business_approval_actions = {'PidEvidenceDecisionView'}

    def post(self, request, extraction_id):
        return Response(review_payload(decide(
            extraction_id, user=request.user, decision=request.data.get('decision'),
            note=request.data.get('note'),
        )))
