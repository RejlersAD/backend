# PR and PO lifecycle in RADAI

Inspected on 24 September 2026. This guide describes the current implementation,
including its limits; it does not introduce a new procurement policy. RADAI uses
Purchase Recommendation and Purchase Requisition for the same PR record.

## Purchase Requisition / Recommendation

1. **Prepare and save a draft.** Select Project or General, record supplier,
   project/department, scope, items, prices, justification and attachments, then
   configure the approvers and **PO Applicable** choice. **Save** retains the
   draft; it does not submit it for approval.
2. **Send for Approval.** This explicit action changes Draft to Submitted and
   requests decisions from the first configured level. Current submission can
   retain advisory completeness and route warnings; an incomplete route is not
   an approval and does not make an unassigned employee eligible to decide.
3. **Complete the assigned decisions.** The lowest unresolved level acts first.
   Every selected approver at that level must approve before the next level
   becomes actionable. A partial route progresses to In Review. Approvers use
   their own saved signature from **Profile > My Signature**.
4. **Finish the PR approval.** Once all configured stages approve, the PR becomes
   Approved. Selecting an employee or linking a document never records their
   approval.
5. **Create the PO when required.** The explicit conversion action requires an
   approved PR, completed configured stages, an active vendor and a positive
   total. It creates one draft PO, preserves PR history and marks the PR
   Converted to PO. Duplicate conversion is rejected.

The form builds these routes from the selected approvers:

| PR type | Approval sequence |
| --- | --- |
| Project | Level 0 Procurement; Level 1 selected employees, all; Level 2 Manager of Engineering, optional; Level 3 Manager of Projects; Level 4 VP Delivery |
| General | Level 0 Procurement; Level 1 selected employees, all; Level 2 configured Vice President |
| Either type, PO Applicable = No | Append CEO approval to the PR route |
| Either type, PO Applicable = Yes | The separate PO carries its own final CEO sign-off |

Fixed business stages require the employee's current business position and
approval access. The configurable PR Level 1 group permits assigned active
employees under its existing record-specific eligibility rules. Administrator
access does not replace a business assignment or let someone skip a stage.

An assigned approver can reject with a reason, making the PR Rejected and
stopping the route. The issuer can refer a rejected PR to MoE or MoP for
resolution, or choose **Edit and resubmit** with existing update access. This
explicit action preserves the rejected review in revision history and opens a
draft with fresh pending assignments. Correct and save the PR, then choose
**Send for Approval** to restart at the first configured level. Earlier approvals
and signatures do not approve the corrected draft. Reopening alone sends no
approval request. The same PR number and original files remain available.

`POST /api/v1/procurement/requisitions/{id}/reopen/` requires the exact saved
`expected_updated_at`; the command rechecks issuer/super-administrator authority,
effective update access and rejected state under a lock. It returns the updated
PR representation. Ordinary PATCH requests cannot edit a rejected PR, reopen it
or replace its archived history. Saves, submission and decisions after reopening
require the current timestamp, so an old tab cannot change or decide a new round.
Cancelled exists as a PR status, but the inspected workflow does not provide a
general cancellation command.

**Linking differs from conversion.** Native PO creation requires an existing PR,
but creation/linking can associate a PR still in Draft, Submitted or In Review.
That association preserves its review requirements. A linked PR becomes Converted
only after its own approvals finish; a PO link cannot complete a pending PR
decision. Legacy POs without a PR remain supported for permitted edits.

## Purchase Order

1. **Prepare the draft PO.** Review the linked PR, supplier, scope, items, prices,
   payment terms, delivery terms and attachments. PR approvals remain source
   history and do not authorize this PO.
2. **Assign and save Final Management Sign-off.** The current configured default
   is Jarmo Suominen. He must still satisfy the active employee, canonical CEO
   position and PO approval-access checks. Saving the assigned PO requests its
   pending approval; selecting his name does not approve it.
3. **Record the PO decision.** The assigned eligible approver opens the request
   and approves with their saved signature or rejects. Approval is recorded
   separately from the PO lifecycle status, so an approved PO may still display
   Draft. Every configured PO stage must approve before progression is allowed.
4. **Record issue and delivery progress.** The supported forward statuses are
   Draft, Sent to Vendor, Acknowledged by Vendor, In Progress, Partially Received
   and Completed. These are available states, not a requirement to visit every
   intermediate state. The server rechecks approval evidence on progression.
5. **Record and confirm receipt.** Goods Receipts shows approved issued POs with
   remaining quantity/service value beside the receipt register. Record actual
   delivered evidence as Pending. The authorized recorder can explicitly confirm
   delivery; configured inspection authority can instead accept or reject it.
   Delivery confirmation does not assert technical inspection checks. Accepted
   evidence determines
   partial versus full coverage; accepting a receipt does not close the PO.
   Mark Complete requires full accepted coverage as well as valid PO approvals.
6. **Reconcile missing historical evidence.** Completed POs without full accepted
   coverage appear in the reconciliation queue. An authorized recorder supplies
   actual receipt/service evidence and a reason; delivery confirmation or
   configured inspection remains a separate decision.
   This preserves the completed PO and its history instead of reopening it.
7. **Capture the supplier invoice.** Finance's awaiting-invoice queue uses
   remaining canonical PO allocations. Import opens with the PO selected and
   requires the actual supplier document and explicit confirmation. Confirmed PO
   links remain visible in the invoice register. Receipt coverage or PO completion
   alone does not verify an invoice or authorize payment. Service-value matching
   and ambiguous historical evidence remain explicit review exceptions.

See [the receiving and invoice contract](PO_RECEIPT_INVOICE_HANDOFF.md) for
balance, freshness, retry and migration requirements. These changes are verified
locally; deployment must apply procurement migrations 0045 and 0046 before the
coordinated backend/frontend release.

A rejected PO approval blocks progression. Cancellation can preserve the record
without creating approval evidence. Completed and Cancelled POs cannot be
reopened through an ordinary status change. Once any PO approval is recorded,
commercial terms are protected; a commercial change requires a revised PO.

**Current delivery limits:** the **Send to Vendor** screen action records the
Sent status. Its current implementation does not itself email or transmit the
document to the supplier. A Sent label is therefore not proof of vendor receipt.
PR/PO approval notifications use RADAI alerts and request Teams delivery, which
depends on configured transport, worker availability and recipient eligibility.
These assignment flows do not request email delivery. Saving or seeing a
notification record does not prove delivery through an external channel.

## Edit the PO Buyer/Seller introduction and download Word

In the PO editor, open **PO Description & Scope** and edit **Buyer / Seller
introduction**. This replaces the standard sentence beginning "We, Rejlers
International Engineering Solutions (Buyer), issue this purchase order to ...".
**Use standard introduction** removes the override and uses the selected supplier.
Clearing the textbox removes the introduction from PDF and Word, including the
current-form preview. The cleared value remains empty after saving and reopening.
Saving uses existing PO update authority. Once commercial content is approved,
the same edit lock applies; the field cannot amend approved terms.

The optional plain text is stored in `contact_persons.order_introduction`, with
a 10,000-character limit and XML-compatible character validation on both writes
and previews. An absent text override retains the standard sentence without
adding a key to legacy records. An explicit empty or whitespace-only value
omits the paragraph without leaving a blank paragraph. No schema migration is required.

Saved `export-word/` and current-form `preview-document/` Word downloads use the
same text as PDF. Native editable Word pages follow the company cover, scope
and price layout. Supporting attachment covers and renderable pages follow in
the same order as the canonical PDF, as page images with their orientation and
dimensions retained. Word's maximum paper size scales oversized drawings; the
editable body can reflow with fonts or Word versions. Attached page images are
not editable text and do not replace the source files.

Both Word routes expose `X-PO-Attachment-Warnings` when a supporting file is
missing, corrupt or unsupported. The cover remains and the download UI warns
that files were omitted. Downloads/previews perform no save or approval and
retain the existing file-source checks; no arbitrary attachment URL is fetched.

The **Show heading** checkbox beside **PO Description & Scope** controls whether
that heading appears in the current preview and saved PDF/Word. Unchecking it
keeps the scope narrative visible. The choice saves in optional Boolean
`contact_persons.show_scope_heading`; existing orders default to showing the
heading without adding metadata. The existing approved-content edit lock still
applies, and this option requires no migration.

An empty narrative no longer repeats the PO title as body text. Clearing both
the Buyer/Seller introduction and narrative omits the whole scope page from
PDF and Word, even if **Show heading** is selected. Empty editor paragraphs or
line breaks do not create a page; images and tables remain content. Populated
scope pages retain their title and the optional heading. The PO narrative uses
a compact toolbar, with separate **Clear formatting** and **Clear text** actions.

**Show introduction** hides or reveals the Buyer/Seller authoring panel and its
paragraph in PDF/Word, retaining the text while hidden. New drafts start hidden;
existing orders without a visibility setting retain their resolved introduction.
An already-empty panel starts collapsed. The optional Boolean
`contact_persons.show_order_introduction` uses the same validation and approved
content lock as other document metadata. Narrative text defaults to **12 pt**;
explicit font sizes remain intact, and commercial/branding typography is unchanged.

## Download a PR in Word format

Open a saved PR review and choose **Download Word**, or use **Download Word** in
the recommendation register's row/detail action menu. The action requires current
Procurement requisition export permission; being assigned to read or approve a
record alone does not grant export access.

`GET /api/v1/procurement/requisitions/{id}/export-word/` returns an editable `.docx`
with the Office Open XML MIME type and an attachment filename. All saved statuses
are supported and printed explicitly. The response is private and not cached.

The document reflects current saved commercial values and recorded approval
evidence. Its editable table layout follows the company PR PDF: bordered
title/logo and field groups, Description/Price/Remarks, and approvals with
Name/Signature/Status/Approval Timestamp, followed by the final timestamp.
Current record status is printed in the footer. Downloading performs no save, submission, approval, notification or
source-file change. Editing the downloaded Word file does not update or approve
the RADAI record. Original uploaded signed PDFs remain separately available;
Word export does not convert or replace them. Unsaved editor inputs are outside
this export contract: save the intended permitted changes before downloading.

## Implementation references

- [PR submission, level sequencing and decisions](../apps/procurement/services/requisition_workflow.py)
- [PR conversion](../apps/procurement/services/requisition_conversion.py) and
  [PR/PO association](../apps/procurement/services/procurement_lifecycle.py)
- [Conditional PR CEO stage](../apps/procurement/services/employee_display.py)
- [PO assignments and decisions](../apps/procurement/services/purchase_order_approvals.py)
- [PO lifecycle guards](../apps/procurement/services/purchase_order_lifecycle.py)
  and [approval controls](PROCUREMENT_APPROVAL_CONTROLS.md)
- [Business eligibility and configured receipt authority](BUSINESS_APPROVAL_ELIGIBILITY.md)
- [API actions and receipt acceptance](../apps/procurement/views.py),
  [record validation](../apps/procurement/serializers.py) and
  [Word document generation](../apps/procurement/services/requisition_word_export.py)

The route table is grounded in the companion frontend's
`src/pages/Procurement/PurchaseRequisitionForm.jsx`; its
`PurchaseOrderDetail.jsx` implements the status-only Send to Vendor action.
The workspace decision register I-04 retains the distinction between current
advisory submission behavior and proposed stricter route requirements.
