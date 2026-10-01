"""Scoped PDF review and feedback commands, independent of proposal approval."""
import hashlib
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID

from django.core import signing
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter
from .models import Deal, Quote, ProposalReviewDocument, ProposalReviewComment, ProposalReviewCommand
from .opportunity_workspace import _actor, require_access, workspace_allowed
from .private_attachments import _file, _identity, download_private_file
from .workflow import _audit


class ReviewError(APIException):
    def __init__(self, code, detail, status=409):
        self.status_code = status
        super().__init__({'code': code, 'detail': detail})


def _uuid(value, field):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({field: 'A valid UUID is required.'}) from None


def _text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or '\x00' in value or (required and not value.strip()):
        raise ValidationError({field: f'Enter {"nonempty " if required else ""}text up to {limit} characters.'})
    return value.strip()


def _user(user):
    return {'id': str(user.pk), 'name': user.get_full_name() or user.username} if user else None


def _quote(quote_id, actor, *actions, lock=False):
    if not all(module_action_allowed(actor, 'sales_proposals', action) for action in ('read', *actions)):
        raise PermissionDenied('You do not have access to this proposal review action.')
    visible = Deal.objects.filter(build_visibility_filter(user=actor, module_code='sales', owner_field='owner'))
    query = Quote.objects.filter(pk=_uuid(quote_id, 'quote_id'), deal__in=visible)
    if lock:
        query = query.select_for_update(of=('self',))
    quote = query.select_related('deal__client', 'client', 'prepared_by').first()
    if quote is None:
        raise NotFound('This proposal is unavailable.')
    require_access(actor, quote.deal)
    if quote.client_id != quote.deal.client_id:
        raise ReviewError('proposal_scope_changed', 'This proposal client no longer matches its opportunity.')
    return quote


def _document(quote, document_id, lock=False):
    query = ProposalReviewDocument.objects.filter(quote=quote, pk=_uuid(document_id, 'document_id'))
    if lock:
        query = query.select_for_update(of=('self',))
    document = query.select_related('created_by', 'attachment__workspace').first()
    if document is None:
        raise NotFound('This review document is unavailable in the selected proposal.')
    return document


def _current(quote):
    return quote.review_documents.order_by('-revision').first()


def _binding_allowed(quote):
    return (quote.status in {'draft', 'scope_development', 'estimation', 'internal_review', 'approval'}
            and not any((quote.approved_at, quote.approved_by_id, quote.submitted_version_hash,
                         quote.sent_date, quote.submission_recipient, quote.submission_evidence)))


def _document_data(document, current_id):
    return {'id': str(document.pk), 'revision': document.revision,
            'file_id': 'radai-' + str(document.attachment_id), 'name': document.name,
            'size': document.size, 'page_count': document.page_count,
            'created_at': document.created_at.isoformat(), 'created_by': _user(document.created_by),
            'is_current': document.pk == current_id, 'feedback_version': document.feedback_version}


def _comment_data(comment):
    return {'id': str(comment.pk), 'parent_id': str(comment.parent_id) if comment.parent_id else None,
            'body': comment.body, 'kind': comment.kind, 'page_number': comment.page_number,
            'anchor': comment.anchor, 'context': comment.context, 'is_resolved': comment.is_resolved,
            'resolved_at': comment.resolved_at.isoformat() if comment.resolved_at else None,
            'resolved_by': _user(comment.resolved_by), 'created_at': comment.created_at.isoformat(),
            'author': _user(comment.author)}


def review_projection(quote_id, actor, document_id=None, cursor=None):
    actor = _actor(actor.pk)
    quote = _quote(quote_id, actor)
    rows = list(quote.review_documents.select_related('created_by').order_by('-revision')[:100])
    current_id = rows[0].pk if rows else None
    document = _document(quote, document_id) if document_id else (rows[0] if rows else None)
    if document and all(row.pk != document.pk for row in rows):
        rows.append(document)
    current = bool(document and document.pk == current_id)
    comments, submissions, next_cursor = [], [], None
    counts, submission_count = {'all': 0, 'open': 0, 'resolved': 0}, 0
    if cursor and not document:
        raise ReviewError('invalid_cursor', 'Refresh this proposal review.', 400)
    if document:
        query = document.comments.all()
        for row in query.filter(parent__isnull=True).values('is_resolved').annotate(total=Count('pk')):
            counts['resolved' if row['is_resolved'] else 'open'] = row['total']
        counts['all'] = counts['open'] + counts['resolved']
        if cursor:
            try:
                payload = signing.loads(cursor, salt='sales-proposal-comments', max_age=900)
                if (payload['quote'], payload['document'], payload['version']) != (
                        str(quote.pk), str(document.pk), document.feedback_version):
                    raise ValueError()
                after_time = parse_datetime(payload['after_time'])
                if after_time is None or not timezone.is_aware(after_time):
                    raise ValueError()
                query = query.filter(Q(created_at__gt=after_time) | Q(created_at=after_time, pk__gt=UUID(payload['after'])))
            except (signing.BadSignature, ValueError, KeyError, TypeError):
                raise ReviewError('invalid_cursor', 'This comment listing changed or expired. Refresh the review.', 409) from None
        page = list(query.select_related('author', 'resolved_by').order_by('created_at', 'pk')[:101])
        comments = [_comment_data(row) for row in page[:100]]
        if len(page) > 100:
            next_cursor = signing.dumps({'quote': str(quote.pk), 'document': str(document.pk),
                'version': document.feedback_version, 'after': str(page[99].pk),
                'after_time': page[99].created_at.isoformat()}, salt='sales-proposal-comments')
        review_rows = document.commands.filter(action='submit').select_related('actor')
        submission_count = review_rows.count()
        submissions = [{'id': str(row.pk), 'outcome': row.outcome, 'note': row.note,
                        'actor': _user(row.actor), 'created_at': row.created_at.isoformat()}
                       for row in review_rows.order_by('-created_at', '-pk')[:100]]
    create = module_action_allowed(actor, 'sales_proposals', 'create')
    update = module_action_allowed(actor, 'sales_proposals', 'update')
    export = workspace_allowed(actor, 'export')
    reason = ('Historical document revisions are read-only.' if document and not current else
              'Opportunity export access is required to preview or download the PDF.' if not export else
              'Approved or submitted proposal document bindings cannot change.' if not document and not _binding_allowed(quote) else
              'Attach a PDF to begin reviewing.' if not document else
              'Your current access does not permit review changes.' if not (create or update) else '')
    capabilities = {'can_preview': bool(document and export), 'can_download': bool(document and export),
                    'can_bind': bool(create and update and export and _binding_allowed(quote)),
                    'can_comment': bool(current and create), 'can_resolve': bool(current and update),
                    'can_submit': bool(current and update), 'deny_reason': reason or None}
    documents_count = quote.review_documents.count()
    latest = _current(quote)
    if ((latest.pk if latest else None) != current_id or (document and not ProposalReviewDocument.objects.filter(
            pk=document.pk, feedback_version=document.feedback_version).exists())):
        raise ReviewError('stale_review', 'The review changed while loading. Refresh its comments.')
    return {'quote': {'id': str(quote.pk), 'quote_number': quote.quote_number, 'version': quote.version,
            'status': quote.status, 'updated_at': quote.updated_at.isoformat(), 'deal_id': str(quote.deal_id),
            'deal_code': quote.deal.deal_code, 'deal_name': quote.deal.deal_name,
            'client_name': quote.client.company_name, 'prepared_by': _user(quote.prepared_by)},
            'documents': [_document_data(row, current_id) for row in rows],
            'documents_count': documents_count,
            'selected_document': _document_data(document, current_id) if document else None,
            'comments': comments, 'next_comments_cursor': next_cursor, 'counts': counts,
            'submissions': submissions, 'submissions_count': submission_count, 'capabilities': capabilities}


def _source(quote, document, actor, export=False):
    attempt = _file(quote.deal, actor, 'proposal', 'radai-' + str(document.attachment_id), *(['export'] if export else []))
    if any(getattr(attempt, field) != getattr(document, field) for field in ('name', 'size', 'sha256')):
        raise ReviewError('source_changed', 'The bound PDF identity changed. Contact your administrator.')
    _identity(attempt)
    return attempt


def review_content(quote_id, actor, document_id):
    actor = _actor(actor.pk)
    quote = _quote(quote_id, actor)
    document = _document(quote, document_id)
    _source(quote, document, actor, export=True)
    content, name = download_private_file(quote.deal, actor, 'proposal', 'radai-' + str(document.attachment_id))
    try:
        current_actor = _actor(actor.pk)
        current_quote = _quote(quote.pk, current_actor)
        current_document = _document(current_quote, document.pk)
        _source(current_quote, current_document, current_actor, export=True)
        if any(getattr(current_document, field) != getattr(document, field) for field in (
                'attachment_id', 'name', 'size', 'sha256', 'page_count')):
            raise ReviewError('source_changed', 'The bound PDF changed while being read. Refresh the review.')
        return content, name
    except Exception:
        content.close()
        raise


def _pdf_pages(content):
    import fitz
    position = content.tell()
    try:
        content.seek(0)
        if content.read(5) != b'%PDF-':
            raise ValidationError({'file_id': 'Choose a valid PDF.'})
        content.seek(0)
        # The private reader has already verified the original bytes. Give
        # MuPDF a disk path instead of materializing the entire PDF in RAM.
        page_count, parser_unavailable = None, False
        with TemporaryDirectory(prefix='radai-proposal-pdf-') as directory:
            path = Path(directory) / 'proposal.pdf'
            with path.open('wb') as target:
                while chunk := content.read(64 * 1024):
                    target.write(chunk)
            try:
                with fitz.open(str(path), filetype='pdf') as pdf:
                    if not (pdf.is_encrypted or pdf.needs_pass or pdf.is_repaired) and 1 <= pdf.page_count <= 500:
                        page_count = pdf.page_count
            except OSError:
                parser_unavailable = True
            except Exception:
                # Exit this handler before deleting the temporary path. A
                # failed MuPDF open can retain its handle in the traceback.
                pass
        if parser_unavailable:
            raise OSError('PDF parser could not access temporary storage.')
        if page_count is None:
            raise ValidationError({'file_id': 'Choose an unencrypted, valid PDF containing 1 to 500 pages.'})
        return page_count
    except OSError:
        raise ReviewError('pdf_validation_unavailable', 'The PDF could not be checked. Retry or contact your administrator.', 503) from None
    finally:
        content.seek(position)


def _anchor(data, page_count):
    page = data.get('page_number')
    if page is not None and (type(page) is not int or not 1 <= page <= page_count):
        raise ValidationError({'page_number': 'Choose a page in this PDF.'})
    anchor = data.get('anchor')
    if anchor is not None:
        if page is None or not isinstance(anchor, dict) or set(anchor) != {'rects', 'quote'}:
            raise ValidationError({'anchor': 'Use page_number and rects/quote for an anchor.'})
        rects = anchor['rects']
        if not isinstance(rects, list) or len(rects) > 50:
            raise ValidationError({'anchor': 'Use at most 50 page rectangles.'})
        _text(anchor['quote'], 'anchor', 2000)
        for rect in rects:
            if not isinstance(rect, dict) or set(rect) != {'x', 'y', 'width', 'height'} or any(
                    type(value) not in (int, float) or not math.isfinite(value) for value in rect.values()):
                raise ValidationError({'anchor': 'Use finite normalized page rectangles.'})
            if (rect['x'] < 0 or rect['y'] < 0 or rect['width'] <= 0 or rect['height'] <= 0
                    or rect['x'] + rect['width'] > 1.000001 or rect['y'] + rect['height'] > 1.000001):
                raise ValidationError({'anchor': 'The anchor must stay inside its PDF page.'})
    return page, anchor


@transaction.atomic
def review_command(quote_id, actor, action, data, document_id=None, comment_id=None):
    actions = {'bind': ('create', 'update'), 'comment': ('create',), 'resolve': ('update',), 'submit': ('update',)}
    fields = {'bind': {'file_id', 'expected_document_id', 'expected_quote_updated_at'},
              'comment': {'body', 'kind', 'page_number', 'anchor', 'context', 'parent_id'},
              'resolve': {'is_resolved'}, 'submit': {'outcome', 'note'}}
    if action not in actions or not isinstance(data, dict) or set(data) - (fields[action] | {'request_id', 'expected_version'}):
        raise ValidationError({'detail': 'Unexpected review command fields.'})
    request_id = _uuid(data.get('request_id'), 'request_id')
    try:
        payload_hash = hashlib.sha256(json.dumps({'action': action, 'document': document_id,
            'comment': comment_id, 'data': data}, sort_keys=True, allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise ValidationError({'detail': 'Invalid review command data.'}) from None
    actor = _actor(actor.pk)
    quote = _quote(quote_id, actor, *actions[action], lock=True)
    if action == 'bind' and not workspace_allowed(actor, 'export'):
        raise PermissionDenied('Opportunity export access is required to bind this PDF.')
    previous = quote.review_commands.filter(request_id=request_id).first()
    if previous:
        if previous.actor_id != actor.pk or previous.payload_hash != payload_hash or previous.action != action:
            raise ReviewError('request_conflict', 'This request ID was already used for different feedback.')
        return {**previous.result, 'replayed': True}, False
    current = _current(quote)
    if action == 'bind':
        if not _binding_allowed(quote):
            raise ReviewError('proposal_immutable', 'Approved or submitted proposal document bindings cannot change.')
        if 'expected_document_id' not in data or data.get('expected_document_id') != (str(current.pk) if current else None):
            raise ReviewError('stale_revision', 'The current document changed. Refresh before attaching this PDF.')
        if data.get('expected_quote_updated_at') != quote.updated_at.isoformat():
            raise ReviewError('stale_quote', 'The proposal changed. Refresh before attaching this PDF.')
        attempt = _file(quote.deal, actor, 'proposal', data.get('file_id'), 'export')
        if not attempt.name.lower().endswith('.pdf'):
            raise ValidationError({'file_id': 'Choose a PDF from this opportunity\'s Proposal folder.'})
        if current and current.attachment_id == attempt.pk:
            raise ReviewError('document_already_bound', 'This PDF is already the current review document.')
        content, _ = download_private_file(quote.deal, actor, 'proposal', 'radai-' + str(attempt.pk))
        with content:
            page_count = _pdf_pages(content)
        actor = _actor(actor.pk)
        quote = _quote(quote.pk, actor, *actions[action], lock=True)
        if not _binding_allowed(quote) or quote.updated_at.isoformat() != data['expected_quote_updated_at']:
            raise ReviewError('stale_quote', 'The proposal changed while reading this PDF. Refresh before binding.')
        fresh = _file(quote.deal, actor, 'proposal', 'radai-' + str(attempt.pk), 'export')
        _identity(fresh)
        if any(getattr(fresh, field) != getattr(attempt, field) for field in ('name', 'size', 'sha256', 'storage_name', 'storage_fingerprint')):
            raise ReviewError('source_changed', 'The PDF identity changed during binding.')
        document = ProposalReviewDocument.objects.create(quote=quote, attachment=fresh,
            revision=current.revision + 1 if current else 1, name=fresh.name, sha256=fresh.sha256,
            size=fresh.size, page_count=page_count, created_by=actor)
    else:
        document = _document(quote, document_id, lock=True)
        _source(quote, document, actor)
        if current is None or document.pk != current.pk:
            raise ReviewError('stale_revision', 'This document revision is historical. Refresh the current review.')
        if type(data.get('expected_version')) is not int or data['expected_version'] != document.feedback_version:
            raise ReviewError('stale_review', 'Feedback changed. Refresh and review your draft before resubmitting.')
    extra, outcome, note = {}, '', ''
    if action == 'comment':
        body = _text(data.get('body'), 'body', 4000, required=True)
        context = _text(data.get('context', ''), 'context', 200)
        kind = data.get('kind', 'comment')
        if kind not in ('comment', 'required_change'):
            raise ValidationError({'kind': 'Choose comment or required_change.'})
        page, anchor = _anchor(data, document.page_count)
        parent = None
        if data.get('parent_id'):
            parent = document.comments.filter(pk=_uuid(data['parent_id'], 'parent_id'), parent__isnull=True).first()
            if parent is None:
                raise ValidationError({'parent_id': 'Reply to a root comment in this document revision.'})
            page, anchor, context, kind = parent.page_number, parent.anchor, parent.context, parent.kind
        comment = ProposalReviewComment.objects.create(document=document, parent=parent, body=body, kind=kind,
            page_number=page, anchor=anchor, context=context, author=actor)
        extra['comment_id'] = str(comment.pk)
    elif action == 'resolve':
        comment = document.comments.filter(pk=_uuid(comment_id, 'comment_id'), parent__isnull=True).first()
        if comment is None:
            raise NotFound('Choose a root comment in this document revision.')
        if type(data.get('is_resolved')) is not bool:
            raise ValidationError({'is_resolved': 'Choose true or false.'})
        comment.is_resolved = data['is_resolved']
        comment.resolved_by = actor if comment.is_resolved else None
        comment.resolved_at = timezone.now() if comment.is_resolved else None
        comment.save(update_fields=['is_resolved', 'resolved_by', 'resolved_at'])
        extra['comment_id'] = str(comment.pk)
    elif action == 'submit':
        outcome = data.get('outcome')
        if outcome not in ('reviewed', 'request_changes'):
            raise ValidationError({'outcome': 'Choose reviewed or request_changes for internal feedback.'})
        note = _text(data.get('note', ''), 'note', 4000)
    if action != 'bind':
        document.feedback_version += 1
        document.save(update_fields=['feedback_version'])
    command = ProposalReviewCommand(quote=quote, document=document, actor=actor, request_id=request_id,
                                   action=action, payload_hash=payload_hash, outcome=outcome, note=note)
    if action == 'submit':
        extra['submission_id'] = str(command.pk)
    result = {'document_id': str(document.pk), 'feedback_version': document.feedback_version,
              'request_id': str(request_id), 'replayed': False, **extra}
    command.result = result
    command.save()
    _audit(quote.deal, actor, 'proposal_review_' + action, data={
        'proposal_id': str(quote.pk), 'document_id': str(document.pk), 'command_id': str(command.pk),
        'feedback_version': document.feedback_version, 'outcome': outcome, **extra})
    return result, True
