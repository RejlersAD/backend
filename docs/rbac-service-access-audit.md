# RADAI module and service access audit

Date: 2026-09-10. Scope: the local RADAI module catalogue, service assignment, frontend route gates, and enquiry operations authorization.

## Findings and changes

| Area | Finding | Change |
| --- | --- | --- |
| Finance | Services shared one broad module code. | Replaced it with Overview, Incoming Invoices, Outgoing Invoices and Salary Slips selections. |
| Sales | Eight service areas shared one module code. | Replaced it with Overview, Opportunities, Proposals, Clients & Contacts, Framework Agreements, Forecasts, Project Handovers and Email Intake selections. |
| Project Control | Planning Package existed in the database but was absent from the assignment group and current sidebar. | Grouped both existing modules together and restored the planning navigation entry and direct-route access. |
| QHSE | Subpages were guarded by the overview module rather than their own assigned service. | Resolve the detailed, quality and health/safety routes to their existing service codes. |
| AI Champion | Present in the database/navigation but missing from the central seed catalogue. | Added the missing catalogue definition. |
| Enquiries | Migration 0048 and the default-role policy granted management to all default users; routes checked only login. An email-based bypass also existed. | Migration 0051 removes only the default-role grant. Admin routes and APIs require an explicit active enquiry-management grant or super-administrator access. Removed the email bypass. |
| Finance access | Customer-invoice APIs and dashboard stats required only login; invoice previews skipped service authorization. An incoming-invoice filter referenced an absent owner field. | Enforce service permissions, apply invoice-preview row filtering, and use `submitted_by` as the invoice owner field. |
| Permission refresh | Frontend route access remained cached for the session. | Recheck on focus, role changes and periodically; preserve component state when grants have not changed. |

## Assignment behavior

- Finance has **4** assignable entries; Sales has **8**; Project Control has **2**.
- Migration 0052 converts existing active Finance/Sales broad grants into explicit service grants, removes the broad assignments and retires their module records. Group checkboxes select individual services; they do not store a broad permission. Access remains cumulative across assigned roles.
- Child service grants do not imply parent or sibling grants. Existing record ownership and workflow permissions still apply.
- Ordinary requesters retain their own enquiry history and response workflow. Being assigned an enquiry, having `is_staff`, or matching an email address does not grant access to the administration register.
- Existing custom roles and unrelated grants are preserved. The refresh does not activate deliberately disabled modules or implement retired/future features.

## Verification

- Backend tests cover service-level read/write denial, broad-grant conversion and retirement, disabled roles/modules, revocation, enquiry list/detail/actions, requester isolation, invoice previews, and idempotent migration behavior.
- Frontend browser tests exercise actual route-guard logic, staff denial, service-only access, catalogue parity and the assignment groups.
- `artifacts/rbac-audit/` contains test/build logs and the local database catalogue audit.

To repeat the database audit:

```text
python manage.py audit_module_access
```

To add missing catalogue entries and refresh module caches without granting roles:

```text
python manage.py audit_module_access --sync
```

Other environments must apply migrations through `rbac.0052_replace_broad_business_grants` and deploy both frontend and backend changes together.
