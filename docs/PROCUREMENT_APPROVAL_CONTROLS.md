# Procurement approval controls

Purchase order lifecycle changes must preserve the approval evidence for the
commercial terms being issued. Linking documents must preserve an active
purchase recommendation's approval requirements.

## Purchase order progression

Sending, acknowledging, progressing, partially receiving, and completing a PO
require a nonempty approval route whose stages are all approved. Known signer
conflicts, changed approved content, unresolved stages and unverified source
evidence block progression. This applies to status writes, dedicated actions
and receipt acceptance. Cancellation remains available without manufacturing an
approval; cancelled/completed orders cannot be reopened by an ordinary status
change.

Older recorded named/assigned approvals remain historical evidence. A signed
source must refer to the linked stored document and its verified evidence;
unresolved reconciliation issues cannot authorize a lifecycle change. Historical
register/document imports may retain an already issued status. Importing that
record does not dispatch it to a vendor, and subsequent lifecycle changes still
use the approval gate.

The serializer exposes `can_send_to_vendor`, `can_complete`, and
`lifecycle_block_reason`. Screens additionally check the user's action
permissions. The server rechecks current data under locks even when a screen
previously showed an available action.

## Linking PR and PO records

Linking a PO to a submitted or in-review PR keeps the existing `po_applicable`
choice and review status. In particular, a pending CEO stage remains required
and actionable when a PO is attached. Drafts remain drafts. Linking an already
approved PR may mark it converted while retaining its recorded native route.

Manual linking, automatic matching, native creation/reassignment and import
paths use the same relationship behavior. A document association is not an
approval decision. PR locks precede PO locks; multi-record imports acquire
their observed PR and PO sets in a stable order and reject stale relationships.

## Approved commercial content

Once any PO approval has been recorded, the editor and spreadsheet overwrite
path reject changes to approved commercial fields, including supplier, price,
currency, tax, line items, payment and delivery terms, scope and contract text.
Use a revised PO for commercial changes. Internal notes, actual delivery,
confirmation and lifecycle metadata remain separate from those terms. No-op
commercial values do not count as a revision.

`purchase_order_content.COMMERCIAL_FIELDS` defines the saved document terms.
New internal approvals and newly verified source approvals store a server-owned
content fingerprint in their approval log entry. The signature display/export,
subsequent approvals and lifecycle gate reject a fingerprint mismatch. Approval
normalization retains the stored fingerprint and ignores a client replacement.
API serialization hides signatures attached to changed commercial content.
Assignment matching treats stage capitalization and surrounding spaces
consistently. Duplicate assignments are rejected so an ordinary route edit
cannot replace a recorded partial approval with a pending duplicate.

Existing approvals are not rewritten or retrospectively certified. They are
protected against future commercial edits, but a fingerprint cannot establish
whether their terms had already changed before these controls were introduced.

## Validation

Regression tests cover guarded status writes and actions, empty/pending/rejected
routes, recorded/source evidence, receipt atomicity, unchanged and revised
commercial fields, stale edits after concurrent approval, signature suppression,
spreadsheet overwrites, and manual/automatic PR links through final CEO review.
Functional test settings use disposable databases and suppress external
delivery. SQLite checks do not certify PostgreSQL row-lock concurrency behavior.
