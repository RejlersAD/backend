"""Read-only analysis of captured conversation evidence within current scope."""

from apps.rbac.action_policy import module_action_allowed

from .email_analysis import MAX_MESSAGES, MAX_TEXT, analyze_email_conversation
from .email_permissions import visible_email_intakes
from .email_opportunity_evidence import source_digest
from .evidence.evidence_engine import build_unified_evidence, document_evidence_records, resolved_value


HISTORY_TEXT_LIMIT = MAX_TEXT // 2
RESPONSE_TEXT_LIMIT = 10 * MAX_TEXT
SOURCE_FIELDS = (
    'id', 'source_message_id', 'internet_message_id', 'subject', 'sender_name',
    'sender_email', 'received_at', 'sent_at', 'body_preview', 'has_attachments',
)


def _message(source, *, selected=False, mailbox_address=''):
    value = source if isinstance(source, dict) else {
        field: getattr(source, field) for field in SOURCE_FIELDS
    }
    sender = (value['sender_email'] or '').strip().casefold()
    own = mailbox_address.strip().casefold()
    return {
        'id': 'selected-message' if selected else f"saved-{value['id']}",
        'internet_message_id': value['internet_message_id'],
        'subject': value['subject'], 'sender_name': value['sender_name'],
        'sender_email': value['sender_email'],
        'received_at': value['received_at'].isoformat() if value['received_at'] else '',
        'sent_at': value['sent_at'].isoformat() if value['sent_at'] else '',
        'body_text': value['body_preview'], 'has_attachments': value['has_attachments'],
        # Captured rows passed the provider incoming check. A changed/invalid
        # envelope cannot become an original merely through saved provenance.
        'direction': 'incoming' if own and sender and sender != own else 'unknown',
    }


def analyze_saved_email(obj, *, request=None, context=None):
    """Never fetch mailbox data, widen source identity, or persist suggestions."""
    context = context if context is not None else {}
    user = getattr(request, 'user', None)
    own = obj.source_mailbox_address or ''
    selected = _message(obj, selected=True, mailbox_address=own)
    messages = [selected]
    coverage = {
        'status': 'saved_content',
        'reason': 'Only saved email text and its quoted chain are available; mailbox history and attachment contents were not fetched.',
    }
    scoped = bool(obj.mailbox_connection_id and (obj.conversation_id or '').strip() and own and obj.source_tenant_id)
    authorized = bool(user and user.is_authenticated and user.is_active)
    if scoped and authorized:
        state = context.setdefault('_saved_email_history', {'histories': {}, 'characters': 0})
        actor = str(user.pk)
        access_key = ('read', actor)
        if access_key not in state:
            state[access_key] = module_action_allowed(user, 'sales_email_intake', 'read')
        if state[access_key]:
            key = (actor, str(obj.mailbox_connection_id), obj.source_tenant_id, own, obj.conversation_id)
            if key not in state['histories']:
                rows, characters, partial = [], 0, False
                remaining_response = max(0, RESPONSE_TEXT_LIMIT - state['characters'])
                if remaining_response:
                    query = visible_email_intakes(user).filter(
                        mailbox_connection_id=obj.mailbox_connection_id,
                        source_tenant_id=obj.source_tenant_id,
                        source_mailbox_address=own,
                        conversation_id=obj.conversation_id,
                        mailbox_connection__tenant_id=obj.source_tenant_id,
                        mailbox_connection__mailbox_address__iexact=own,
                    ).order_by('received_at', 'pk').values(*SOURCE_FIELDS)[:MAX_MESSAGES + 1]
                    for row in query.iterator(chunk_size=1):
                        if len(rows) >= MAX_MESSAGES:
                            partial = True
                            break
                        remaining = min(HISTORY_TEXT_LIMIT, remaining_response) - characters
                        body = row['body_preview'] or ''
                        if len(body) > remaining:
                            row['body_preview'] = body[:max(0, remaining)]
                            rows.append(row)
                            characters += len(row['body_preview'])
                            partial = True
                            break
                        rows.append(row)
                        characters += len(body)
                else:
                    partial = True
                state['characters'] += characters
                state['histories'][key] = (rows, partial)
            rows, partial = state['histories'][key]
            siblings = [row for row in rows if row['id'] != obj.pk]
            if len(siblings) >= MAX_MESSAGES:
                partial = True
            messages.extend(_message(row, mailbox_address=own) for row in siblings[:MAX_MESSAGES - 1])
            coverage = {
                'status': 'partial' if partial else 'saved_content',
                'reason': (
                    'Analysis uses available saved incoming messages from this mailbox conversation and their quoted chains. '
                    'Outgoing or uncaptured messages, attachments and portal documents may be missing.'
                    + (' Saved conversation evidence exceeded the response limit; some history was omitted.' if partial else '')
                ),
            }
    result = analyze_email_conversation(
        messages, selected_message_id='selected-message', coverage=coverage, mailbox_address=own,
    )
    view = context.get('view')
    attachments = ([] if getattr(view, 'action', None) == 'list'
                   else list(obj.source_attachments.order_by('source_part_index', 'pk')))
    attached_records = []
    for attachment in attachments:
        chunks = attachment.text_chunks if isinstance(attachment.text_chunks, list) else []
        parsed_chunks = [(item.get('location', 'Document text'), item.get('text', ''))
                         for item in chunks if isinstance(item, dict) and isinstance(item.get('text'), str)]
        attached_records.extend(document_evidence_records(
            attachment.name, parsed_chunks, 'attachment:' + str(attachment.pk),
        ))
    email_evidence = result['extracted_information'].get('unified_evidence') or {}
    unified_evidence = build_unified_evidence([*(email_evidence.get('records') or []), *attached_records])
    fields = {
        'customer_name': 'customer_name', 'organization_name': 'organization_name', 'project_name': 'project_name',
        'tender_reference': 'tender_reference', 'scope_summary': 'scope_summary', 'scope_type': 'scope_type',
        'estimated_value': 'estimated_value', 'currency': 'currency', 'due_date': 'due_date',
        'expected_award_date': 'expected_award_date',
    }
    for field, target in fields.items():
        result['extracted_information'][target] = resolved_value(unified_evidence, field)
    result['extracted_information']['company_name'] = result['extracted_information'].get('organization_name') or result['extracted_information'].get('customer_name', '')
    result['extracted_information']['deadline_date'] = result['extracted_information'].get('due_date', '')
    result['extracted_information']['unified_evidence'] = unified_evidence
    result['analysis']['unified_evidence'] = unified_evidence
    if attachments:
        read_count = sum(bool(row.text_chunks) for row in attachments)
        result['analysis']['summary'] = (result['analysis'].get('summary', '') +
            f' {read_count} of {len(attachments)} attached document(s) supplied readable text for evidence review.')
        if read_count < len(attachments):
            result['analysis']['limitations'].append('One or more attachments could not be fully read; review the listed file status.')
        result['analysis']['coverage']['documents_reviewed'] = read_count
        result['analysis']['limitations'] = [
            item for item in result['analysis']['limitations']
            if item != 'Attachment and linked-portal document contents were not read. Dates or requirements stated only there remain unconfirmed.'
        ]
        result['analysis']['suggested_actions'] = [
            item for item in result['analysis']['suggested_actions']
            if item.get('text') != 'Review the referenced attachments or portal documents.'
        ]
        if read_count < len(attachments):
            result['analysis']['limitations'].append(
                'One or more attached documents could not be fully read; their contents may be missing from evidence review.'
            )
        elif attachments:
            result['analysis']['summary'] += ' Attachment evidence is included in the unified evidence below.'
    if context.get('include_assistant_sources'):
        from .email_review_assistant import assistant_sources
        context['assistant_sources'] = assistant_sources(result, messages)
    context.setdefault('_saved_email_source_hashes', {})[str(obj.pk)] = source_digest({
        'mailbox': own, 'tenant': obj.source_tenant_id,
        'connection': str(obj.mailbox_connection_id or ''),
        'messages': messages, 'coverage': coverage,
        'attachments': [(str(row.pk), row.sha256, row.extraction_status, row.text_chunks) for row in attachments],
    })
    return {**result['extracted_information'], 'analysis': result['analysis']}
