"""Human-confirmed exact matching for the opt-in local archive pilot."""
import re

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import NotFound

from .email_client_resolution import require_reviewed_customer_name
from .email_customer_matching import NAME_FIELDS, normalize_customer_name
from .email_opportunity_evidence import read_saved_email_review
from .email_permissions import (
    require_email_opportunity_access, visible_email_clients, visible_email_intakes,
    visible_email_opportunities,
)
from .mailbox_opportunities import EmailReviewConflict
from .models import Client, Deal, OpportunityAuditEvent
from .opportunity_workspace import require_access
from .saved_email_analysis import analyze_saved_email


def require_pilot(intake):
    if (not getattr(settings, 'SALES_TEST_ARCHIVE_FILES_ENABLED', False)
            or settings.ENVIRONMENT not in {'development', 'local', 'testing', 'test'}
            or intake.mailbox_connection_id
            or not re.fullmatch(r'onedrive-test:[a-f0-9]{64}', intake.source_message_id)
            or 'RADAI-TEST' not in intake.subject.upper()):
        raise NotFound('Local test archive matching is unavailable.')


def exact_matches(intake, request):
    """Only explicit sourced company names and tender references may match."""
    require_pilot(intake)
    require_email_opportunity_access(request.user)
    context = {'email_ai_skip': True}
    snapshot = analyze_saved_email(intake, request=request, context=context)
    name = snapshot.get('organization_name') if snapshot.get('detection_version') == 2 else snapshot.get('customer_name')
    try:
        require_reviewed_customer_name({'new_client': {'company_name': name}}, snapshot)
    except serializers.ValidationError:
        return [], context
    reference = snapshot.get('tender_reference')
    evidence = snapshot.get('evidence', {})
    if (not isinstance(reference, str) or not reference.strip() or len(reference) > 120
            or not re.search(r'\d', reference) or not evidence.get('tender_reference')
            or not snapshot.get('field_sources', {}).get('tender_reference')):
        return [], context
    normalized = normalize_customer_name(name)
    clients = []
    for row in Client.objects.values('pk', *NAME_FIELDS).iterator(chunk_size=500):
        if any(normalize_customer_name(row[field]) == normalized for field in NAME_FIELDS):
            clients.append(row['pk'])
            if len(clients) > 1:
                return [], context
    if len(clients) != 1 or not visible_email_clients(request.user).filter(pk=clients[0]).exists():
        return [], context
    # Mutable manual references are candidates for human review, never automatic authority.
    matches = list(visible_email_opportunities(request.user).filter(
        client_id=clients[0], client_reference__iexact=reference.strip(),
    ).order_by('pk')[:2])
    return matches, context


def link_reviewed_match(intake, request):
    require_pilot(intake)
    require_email_opportunity_access(request.user)
    target_id = serializers.UUIDField().run_validation(request.data.get('opportunity'))
    from .email_classification_review import classification_review_evidence, require_classification_review
    classification = require_classification_review(request.data)
    with transaction.atomic():
        locked = visible_email_intakes(request.user).select_for_update().get(pk=intake.pk)
        if locked.opportunity_id:
            if locked.opportunity_id != target_id:
                raise EmailReviewConflict('This email is already linked to another opportunity.')
            opportunity = visible_email_opportunities(request.user).filter(pk=target_id).first()
            if opportunity is None:
                raise NotFound('The opportunity is unavailable.')
            require_access(request.user, opportunity, 'create', 'update')
            if not visible_email_clients(request.user).filter(pk=opportunity.client_id).exists():
                raise NotFound('The linked client is unavailable.')
            previous = OpportunityAuditEvent.objects.filter(
                opportunity=opportunity, actor=request.user, event_type='opportunity_linked_from_email',
                data__source_email_intake_id=str(locked.pk),
            ).first()
            if not previous or previous.data.get('reviewed_classification', {}).get('code') != classification:
                raise EmailReviewConflict('This email already has a different reviewed link.')
            return locked
        if locked.status not in {'received', 'under_review'}:
            raise EmailReviewConflict('This email cannot be linked in its current state.')
        # Serialize canonical directory/reference writes during match revalidation.
        # Do not call a model or perform storage I/O while holding these locks.
        from django.db import connection
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                for model in (Client, Deal):
                    cursor.execute(
                        f'LOCK TABLE {connection.ops.quote_name(model._meta.db_table)} IN SHARE ROW EXCLUSIVE MODE')
        matches, context = exact_matches(locked, request)
        if len(matches) != 1 or matches[0].pk != target_id:
            raise EmailReviewConflict('No unique exact client/reference match remains. Review this email manually.')
        if not request.data.get('source_token'):
            raise serializers.ValidationError({'source_token': 'Reload the email before linking.'})
        snapshot = read_saved_email_review(
            request.data['source_token'], locked, request.user,
            context['_saved_email_source_hashes'][str(locked.pk)],
        )
        opportunity = matches[0]
        require_access(request.user, opportunity, 'create', 'update')
        OpportunityAuditEvent.objects.create(
            opportunity=opportunity, actor=request.user, event_type='opportunity_linked_from_email',
            data={'source_email_intake_id': str(locked.pk), 'source_message_id': locked.source_message_id,
                  'reviewed_classification': classification_review_evidence(classification, request.user),
                  'reviewed_email_analysis': snapshot, 'match_method': 'reviewed_exact_client_reference'},
        )
        locked.opportunity = opportunity
        locked.status = 'converted'
        locked.reviewed_by = request.user
        locked.reviewed_at = timezone.now()
        locked.save(update_fields=['opportunity', 'status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        return locked
