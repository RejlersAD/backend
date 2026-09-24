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
resolution. There is no general return-to-draft/resubmit command implied by this
guide: submission accepts drafts, and ordinary edits cannot erase decisions.
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
5. **Record and inspect receipt.** Goods Receipts capture delivered items and
   inspection evidence. Acceptance requires the configured receipt authority and
   valid PO approvals. The current acceptance command marks the receipt Accepted
   and the PO Completed, with actual delivery dated that day. The PO screen also
   supports manual completion after its approval guard passes. Do not interpret
   this as an implemented line-balance or invoice-payment closure policy.

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
