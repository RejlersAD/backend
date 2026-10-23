"""Reusable application links create isolated applicant drafts, not shared forms."""
import secrets
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response

from .governance import audit
from .models import RecruitmentApplicationLink, RecruitmentFormInvitation, RecruitmentOpening
from .recruitment_forms import ClosedCVLink, HRFormsView, PublicFormView, digest, invitation_link, summary


def public_link_summary(link):
    return {'id': link.pk, 'kind': link.kind, 'opening': link.opening_id,
            'position': link.opening.title if link.opening_id else ('General CV submission' if link.kind == 'cv' else 'All open positions'),
            'expires_at': link.expires_at, 'revoked': bool(link.revoked_at),
            'submitted_applications': link.applications.filter(candidate__isnull=False).count()}


def active_link(link):
    if link.revoked_at or link.expires_at <= timezone.now():
        raise PermissionDenied('This application link has expired or was revoked. Please contact HR.')
    if link.opening_id and not link.opening.is_open:
        raise PermissionDenied('This position is closed. Please contact HR.')
    if link.kind == 'cv' and link.applications.filter(submissions__isnull=False).exists():
        raise ClosedCVLink()


class RecruitmentApplicationLinksView(HRFormsView):
    def get(self, request):
        links = RecruitmentApplicationLink.objects.select_related('opening').order_by('-created_at')
        link_id = request.query_params.get('link')
        if link_id:
            try:
                link = links.get(pk=link_id)
            except (RecruitmentApplicationLink.DoesNotExist, ValueError, TypeError):
                raise NotFound()
            applications = link.applications.filter(candidate__isnull=False).select_related('candidate', 'opening').order_by('-created_at')
            return Response({**public_link_summary(link), 'applications': [{**summary(item), 'candidate_name': item.candidate.name} for item in applications]})
        return Response([public_link_summary(link) for link in links])

    @transaction.atomic
    def post(self, request):
        kind = request.data.get('kind')
        if kind not in ('cv', 'pre_onboarding'):
            raise ValidationError({'kind': 'Choose CV or pre-onboarding.'})
        opening = None
        if request.data.get('opening') not in (None, ''):
            try:
                opening = RecruitmentOpening.objects.get(pk=request.data['opening'], is_open=True)
            except (RecruitmentOpening.DoesNotExist, ValueError, TypeError):
                raise ValidationError({'opening': 'Choose an open position, or all open positions.'})
        token = secrets.token_urlsafe(32)
        link = RecruitmentApplicationLink.objects.create(kind=kind, opening=opening, owner=request.user,
            token_hash=digest(token), expires_at=timezone.now() + timedelta(days=3))
        url = invitation_link(token).replace('#invite=', '#apply=')
        audit(actor=request.user, action='recruitment.application_link.created', object_type='recruitment_application_link', object_id=link.pk,
              metadata={'kind': kind, 'opening_id': link.opening_id}, request=request)
        return Response({**public_link_summary(link), 'link': url}, status=201)


class RecruitmentApplicationLinkRevokeView(HRFormsView):
    permission_action = 'update'

    @transaction.atomic
    def post(self, request, pk):
        try:
            link = RecruitmentApplicationLink.objects.select_for_update().select_related('opening').get(pk=pk)
        except RecruitmentApplicationLink.DoesNotExist:
            raise NotFound()
        link.revoked_at = link.revoked_at or timezone.now()
        link.save(update_fields=['revoked_at', 'updated_at'])
        audit(actor=request.user, action='recruitment.application_link.revoked', object_type='recruitment_application_link', object_id=link.pk, request=request)
        return Response(public_link_summary(link))


class ApplicantRegistration(serializers.Serializer):
    name = serializers.CharField(max_length=200)
    email = serializers.EmailField(max_length=254)
    opening = serializers.IntegerField(min_value=1)


class PublicApplicationAccessView(PublicFormView):
    @transaction.atomic
    def post(self, request):
        token = request.data.get('token', '')
        if not isinstance(token, str) or not 32 <= len(token) <= 100:
            raise PermissionDenied('Invalid application link. Please contact HR.')
        try:
            link = RecruitmentApplicationLink.objects.select_for_update(of=('self',)).select_related('opening').get(token_hash=digest(token))
        except RecruitmentApplicationLink.DoesNotExist:
            raise PermissionDenied('Invalid application link. Please contact HR.')
        active_link(link)
        # Both public forms open isolated blank drafts, without registration.
        if not request.data.get('register'):
            personal_token, session = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            invitation = RecruitmentFormInvitation.objects.create(
                application_link=link, opening=link.opening, owner=link.owner, kind=link.kind,
                recipient='', token_hash=digest(personal_token), expires_at=link.expires_at,
                draft={'candidate': {}} if link.kind == 'cv' else {'identity': {}, 'contact': {}, 'beneficiary': {'date': timezone.localdate().isoformat()}}, session_hash=digest(session),
                session_expires_at=min(timezone.now() + timedelta(hours=1), link.expires_at))
            return Response({'token': personal_token, 'session': session,
                'resume_link': invitation_link(personal_token), 'expires_at': invitation.expires_at}, status=201)
        available = RecruitmentOpening.objects.filter(is_open=True)
        if link.opening_id:
            available = available.filter(pk=link.opening_id)
        serializer = ApplicantRegistration(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            opening = available.get(pk=data['opening'])
        except RecruitmentOpening.DoesNotExist:
            raise ValidationError({'detail': 'This position is no longer available. Choose an open position.'})
        personal_token, session = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = timezone.now()
        draft = {'candidate': {'name': data['name'], 'email': data['email']}} if link.kind == 'cv' else {
            'contact': {'email': data['email']}, 'beneficiary': {'candidate_name': data['name'], 'date': timezone.localdate().isoformat()}}
        invitation = RecruitmentFormInvitation.objects.create(application_link=link, opening=opening, owner=link.owner,
            kind=link.kind, recipient=data['email'], token_hash=digest(personal_token), expires_at=link.expires_at,
            draft=draft, session_hash=digest(session) if link.kind == 'cv' else '',
            session_expires_at=min(now + timedelta(hours=1), link.expires_at) if link.kind == 'cv' else None)
        result = {'token': personal_token, 'resume_link': invitation_link(personal_token)}
        if link.kind == 'pre_onboarding':
            result['verification_required'] = True
        else:
            result['session'] = session
        return Response(result, status=201)
