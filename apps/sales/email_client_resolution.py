"""Resolve a reviewed email customer within the opportunity transaction."""

from django.db import connection
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from apps.rbac.action_policy import module_action_allowed

from .email_customer_matching import CONFLICT, NAME_FIELDS, normalize_customer_name
from .email_permissions import visible_email_clients
from .models import Client, Contact


LEGACY_CLIENT_FIELDS = {
    'company_name', 'industry_type', 'email', 'phone', 'website', 'country',
    'contact_name', 'contact_email',
}


def email_customer_intent(data, *, legacy=False):
    """Accept one existing ID or an explicitly reviewed company name."""
    client_id, proposed = data.get('client'), data.get('new_client')
    if client_id and proposed is not None:
        raise serializers.ValidationError({'client': 'Choose one customer for this opportunity.'})
    if client_id:
        try:
            return {'client': serializers.UUIDField().run_validation(client_id)}
        except serializers.ValidationError:
            raise serializers.ValidationError({'client': 'Select an accessible client.'}) from None
    allowed = LEGACY_CLIENT_FIELDS if legacy else {'company_name'}
    if (not isinstance(proposed, dict) or set(proposed) - allowed
            or not isinstance(proposed.get('company_name'), str)):
        raise serializers.ValidationError({'client': 'Select a customer or use the detected company name.'})
    try:
        name = serializers.CharField(max_length=300, allow_blank=False).run_validation(proposed.get('company_name'))
    except serializers.ValidationError:
        raise serializers.ValidationError({'client': 'Provide a company name of at most 300 characters.'}) from None
    return {'new_client': {**proposed, 'company_name': name}}


def require_reviewed_customer_name(intent, snapshot):
    """The signed explicit buyer claim permits no sender/domain-name fallback."""
    if 'new_client' not in intent:
        return
    key = 'organization_name' if snapshot.get('detection_version') == 2 else 'customer_name'
    name = snapshot.get(key)
    evidence = snapshot.get('evidence', {})
    sources = snapshot.get('field_sources', {})
    excerpt = evidence.get(key) if isinstance(evidence, dict) else None
    references = sources.get(key) if isinstance(sources, dict) else None
    warnings = snapshot.get('warnings', [])
    normalized = normalize_customer_name(name)
    if (
        not normalized or len(name) > 300
        or not isinstance(excerpt, str) or not excerpt.strip()
        or normalized not in normalize_customer_name(excerpt)
        or not isinstance(references, list) or not references
        or any(not isinstance(ref, str) or not ref for ref in references)
        or normalize_customer_name(intent['new_client']['company_name']) != normalized
        or (isinstance(warnings, list) and any(isinstance(item, str) and CONFLICT.search(item) for item in warnings))
    ):
        raise serializers.ValidationError({
            'client': 'Use the source-backed detected company name or select an accessible client.',
        })


def _resolution_conflict():
    # Do not reveal inaccessible customer identities, counts or ownership.
    from .mailbox_opportunities import EmailReviewConflict
    raise EmailReviewConflict(
        'The customer cannot be resolved safely. Select an accessible client and try again.',
        code='email_customer_conflict',
    )


def resolve_email_customer(user, intent, *, legacy=False):
    """Caller holds its source lock and an atomic transaction; no network here.

    Name resolution locks the client table against concurrent writers, including
    generic Client API inserts. It is deliberately not a global name-uniqueness
    rule: generic writes after this transaction may still create ambiguous names.
    """
    if not connection.in_atomic_block:
        raise RuntimeError('Email customer resolution requires a transaction.')
    if 'client' in intent:
        client = visible_email_clients(user).select_for_update().filter(pk=intent['client']).first()
        if client is None:
            raise serializers.ValidationError({'client': 'Select an accessible client.'})
        return client, {'mode': 'selected', 'client_id': str(client.pk), 'created': False}

    proposed = intent['new_client']
    name = proposed['company_name']
    normalized = normalize_customer_name(name)
    if connection.vendor == 'postgresql':
        with connection.cursor() as cursor:
            table = connection.ops.quote_name(Client._meta.db_table)
            cursor.execute(f'LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE')
    matches = []
    for row in Client.objects.order_by('pk').values('pk', *NAME_FIELDS).iterator(chunk_size=500):
        if any(normalize_customer_name(row[field]) == normalized for field in NAME_FIELDS):
            matches.append(row['pk'])
            if len(matches) > 1:
                _resolution_conflict()
    if matches:
        client = visible_email_clients(user).select_for_update().filter(pk=matches[0]).first()
        if client is None:
            _resolution_conflict()
        return client, {'mode': 'exact_name', 'client_id': str(client.pk), 'reviewed_name': name, 'created': False}

    if not module_action_allowed(user, 'sales_clients', 'create'):
        raise PermissionDenied('You do not have access to create clients.')
    from .serializers import ClientCreateSerializer
    payload = {'company_name': name, 'industry_type': 'other', 'status': 'prospect'}
    if legacy:
        for key in ('industry_type', 'email', 'phone', 'website', 'country'):
            if proposed.get(key):
                payload[key] = proposed[key]
    serializer = ClientCreateSerializer(data=payload)
    serializer.is_valid(raise_exception=True)
    client = serializer.save(account_manager=user)
    if legacy and proposed.get('contact_email'):
        email = serializers.EmailField().run_validation(proposed['contact_email'])
        parts = str(proposed.get('contact_name') or '').strip().split()
        Contact.objects.create(
            client=client, first_name=parts[0] if parts else 'Email',
            last_name=' '.join(parts[1:]) if len(parts) > 1 else 'Contact',
            email=email, phone=proposed.get('phone') or '', role_type='procurement', is_primary=True,
        )
    return client, {'mode': 'created', 'client_id': str(client.pk), 'reviewed_name': name, 'created': True}
