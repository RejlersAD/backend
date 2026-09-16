"""Check recorded approval identities without rewriting historical evidence."""

from collections import Counter

from rest_framework.exceptions import ValidationError


def protect_approval_route(existing, incoming, *, level, label, freeze_route=False):
    """Keep submitted levels and completed assignments intact during form edits."""
    previous = [row for row in existing if isinstance(row, dict) and not row.get('external') and not row.get('evidence_document_id')]
    proposed = [row for row in incoming if isinstance(row, dict) and not row.get('external') and not row.get('evidence_document_id')]

    def position(row, index):
        return level(row, index), str(row.get(label) or '').strip().lower()

    if freeze_route and Counter(position(row, index) for index, row in enumerate(previous)) != Counter(
        position(row, index) for index, row in enumerate(proposed)
    ):
        raise ValidationError('Approval levels and positions cannot be removed, added, or moved after submission.')
    for index, row in enumerate(previous):
        if str(row.get('status') or 'pending').strip().lower() in {'pending', 'in_review'}:
            continue
        candidates = [candidate for next_index, candidate in enumerate(proposed)
                      if position(row, index) == position(candidate, next_index)]
        email = str(row.get('user_email') or row.get('approver_email') or row.get('email') or '').strip().lower()
        identifier = str(row.get('user_id') or row.get('approver_id') or '')
        if not any(
            (str(candidate.get('user_email') or candidate.get('approver_email') or candidate.get('email') or '').strip().lower() == email)
            if email else (str(candidate.get('user_id') or candidate.get('approver_id') or '') == identifier)
            for candidate in candidates
        ):
            raise ValidationError('An approval with a recorded decision cannot be removed, moved, or reassigned.')


def stage_signature_issue(stage):
    """Return a review reason when saved signer metadata contradicts assignment.

    Email is the stable assignment identity for migrated records. Old rows
    without signer metadata remain historical evidence, not a guessed mismatch.
    External, reviewed source documents have their own evidence-verification path.
    """
    if stage.get('external') or stage.get('evidence_document_id'):
        return ''
    assigned_email = str(stage.get('user_email') or stage.get('approver_email') or stage.get('email') or '').strip().lower()
    assigned_id = str(stage.get('user_id') or stage.get('approver_id') or '')
    actor_email = str(stage.get('approved_by_email') or stage.get('decided_by_email') or '').strip().lower()
    actor_id = str(stage.get('approved_by_id') or stage.get('decided_by_id') or '')
    signature_email = str(stage.get('signature_user_email') or '').strip().lower()
    signature_id = str(stage.get('signature_user_id') or '')

    def conflicts(first_email, first_id, second_email, second_id):
        if first_email and second_email:
            return first_email != second_email
        return bool(first_id and second_id and first_id != second_id)

    if conflicts(assigned_email, assigned_id, actor_email, actor_id):
        return 'The recorded signer does not match the assigned approver. Approval review is required.'
    if conflicts(assigned_email, assigned_id, signature_email, signature_id):
        return 'The signature owner does not match the assigned approver. Approval review is required.'
    if conflicts(actor_email, actor_id, signature_email, signature_id):
        return 'The signature owner does not match the recorded signer. Approval review is required.'
    return ''


def purchase_order_signature_issue(order):
    """Validate the final signature against recorded internal PO decisions."""
    rows = [row for row in (getattr(order, 'approval_log', None) or [])
            if isinstance(row, dict) and (row.get('user_id') or row.get('approver_email'))
            and not row.get('external') and not row.get('evidence_document_id')]
    for row in rows:
        issue = stage_signature_issue(row)
        if issue:
            return issue
    if not rows or not getattr(order, 'approval_signature', ''):
        return ''
    if any(str(row.get('status', '')).strip().lower() != 'approved' for row in rows):
        return 'The approval sequence is incomplete. Final signature review is required.'
    final_actor_id = str(getattr(order, 'approved_by_id', '') or '')
    if not final_actor_id:
        return 'The final signature has no recorded signer identity. Approval review is required.'

    def row_level(row, index):
        try:
            return max(0, int(row.get('level')))
        except (TypeError, ValueError):
            return index

    final_level = max(row_level(row, index) for index, row in enumerate(rows))
    matching = [row for index, row in enumerate(rows)
                if row_level(row, index) == final_level
                and str(row.get('approved_by_id') or row.get('user_id') or '') == final_actor_id]
    if not matching or not any(row.get('signature') == order.approval_signature for row in matching):
        return 'The final signature does not match the recorded approver. Approval review is required.'
    return ''
