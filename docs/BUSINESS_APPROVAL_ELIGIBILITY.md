# Business approval eligibility

An approval request is actionable only when all three conditions hold at the time of delivery and decision:

1. The authenticated active employee holds the designated business position or a controlled current responsibility for this record.
2. Their current effective module permission permits approval. Explicit denies override inherited grants.
3. The record is pending at that person's stage, all required predecessors are complete, and the assignment/version is current.

Super Admin, staff, CEO and application access roles do not supply a missing business assignment or skip a stage. Existing access grants are not changed by this release. Organizational positions come from the HR employee master, not an editable profile label or an access-role name. An assigned project manager or reporting manager is a business responsibility, not an admin permission.

## Where decisions are enforced

`apps/rbac/approval_eligibility.py` provides current access, canonical position, project assignment and configured route checks. `route_guard.py` requires an explicitly registered, checked approval command. Generic CRUD cannot manufacture, clear or reset recorded decisions. Write requests keep checks and changes in one transaction; detail approval requests lock their record.

Domain services additionally check their own assignment, stage, predecessor, self-approval and evidence rules. Never register a new `business_approval_actions` command without those checks. Registration is a code contract, not an authorization grant. Test both a legitimate decision and each missing gate.

| Workflow | Business assignment source | Sequence checks |
| --- | --- | --- |
| PR / PO | Recorded stage assignee plus required organizational position | Current lowest pending level, completed predecessors, current assignment identity |
| Leave / overtime / HR tasks | Canonical reporting manager and configured HR workflow stage | Current instance and stage; no missing-manager or superuser shortcut |
| Payroll engine | HR and Finance positions for their respective stages | HR before Finance before release |
| Invoice | Authenticated user matching the unique assigned approver identity | Pending invoice and approval; all lower-level peers completed |
| Salary approval | Explicit named route / recorded current approval row | Pending current step and approved predecessors |
| Offboarding PM decisions | Current project manager assignments | All required current peers; historical notifications grant no authority |
| Planning / proposal | Assigned task reviewer or current project owner / project manager | Current version, review stages and final authority; stale approvals rejected |
| Project Control | Current project responsibility or designated commercial position | Existing submission, independence and approved-source evidence gates |
| Profile documents | Designated HR / administration reviewer | Active pending document owned by another employee |
| Access requests | ICT business position with access-management approval permission | Pending request from another user |
| Enquiry | Assigned head of the enquiry's catalog department | Pending required approval on an open enquiry |
| Site visits | Canonical employee reporting manager | Pending request; employee cannot approve own request |

## Notifications and screens

Approval notifications carry typed record/task identifiers. The sender filters recipients and the transport revalidates each delivery attempt, including retries, against the same business eligibility. A stale notification remains history but loses `requires_action`; the approval screen refreshes the record and the backend still checks the decision.

Supported notification contexts include PR, PO, HR workflow task, legacy leave, proposal task, offboarding project-manager decision, finance payroll workflow and enquiry approval. Unsupported actionable `APPROVAL` notifications are suppressed, not sent to administrators as a fallback. Informational result notices must set `requires_action: false`. New approval notification families need a domain eligibility adapter in `apps/notifications/delivery.py` before sending. Invoice email links require login and do not themselves grant authority.

## Legacy workflows without a business route

Some older single-step models store a status but no designated approval responsibility. They now return HTTP 403 with the missing route key until a controlled policy is configured. This applies to receipt accept/reject, procurement budget approval, electrical datasheet approval/rejection/revision, CRS document approval, PID issue approval/ignore, PFD conversion approval, Sales bid/award decisions, framework activation, proposal approval and forecast approval. Other unregistered decision commands are also denied by the central API guard.

Set `RADAI_BUSINESS_APPROVAL_ROUTES` as a Django settings dictionary or a JSON environment value. It is deployment-controlled configuration; requests cannot supply or override it. The key is `<module_code>.<ModelClass>.<operation>`.

Example schema only; this is **not an approved RAD policy**:

```json
{
  "electrical_datasheet.ElectricalDatasheet.approve": {
    "positions": ["hod_electrical"],
    "state_field": "status",
    "pending_states": ["under_review"]
  }
}
```

`positions` and `pending_states` must be nonempty lists. Position codes resolve to explicit official title aliases; generic Admin/Manager/VP access labels do not confer unrelated authority. Optional `assignee_field` requires that object's user-ID field to match the actor; `department_field` additionally restricts the route to the canonical employee department; `submitter_field` forbids self-approval. Use actual model attributes, including `_id` for foreign keys. Sales Deal routes need `state_field: "stage"`. Configure each decision operation separately. Multi-stage processes require their domain workflow, not a permissive single-step route.

Generic numeric PR stages also need an explicit `business_position` from the organizational role catalog. The form requires it for Level 1; the server does not infer a position from whichever person happens to be selected. Submitted workflows cannot be rewritten by a routine record edit.

## Rollout and data corrections

This change adds no schema migrations and does not rewrite existing decisions, signatures, employee assignments, RBAC grants or workflow policies. Missing/ambiguous employee links, official positions, reporting managers or approval routes must be corrected through controlled administration. Requests remain blocked while a required gate is missing. Sales handover requires an explicitly nominated project manager; a Sales owner is not automatically made a delivery approver.

Two existing workflow limitations remain deliberately blocked: daily work-log project-manager approvals have no controlled project-manager assignment field, and configured offboarding HR coordinator/HR approver rows have no decision endpoint in the existing application. They cannot be treated as completed or skipped. Enabling those paths needs an explicit assignment workflow or the missing decision UI/API, with the same three checks.

Validate configured routes with a legitimate approver and wrong-position, missing-permission, premature-stage, replay and changed-assignment cases. Do not backfill approvals merely to unblock later stages. Historical invalid decisions/signatures require a separate audited data correction.

Tests use disposable databases and disabled external delivery. Functional SQLite tests cover business rules; the guarded authorization suite covers HTTP enforcement. PostgreSQL checks cover locking behavior and migration consistency separately. Frontend capability/browser tests verify actionable buttons follow server eligibility.
