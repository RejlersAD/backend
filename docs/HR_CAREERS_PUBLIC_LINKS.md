# HR-REC-03 - Careers synchronization and candidate-free links

Authorized 22 October 2026. Both forms support reusable candidate-free links.
HR flow: **Create invitation link -> choose CV or Pre-Onboarding -> Create link
-> Copy link**. No candidate, position, mode, preview, email or history steps
are included in this dialog. Candidate rows offer a separate **Review submitted
forms** action; interview outcome is maintained in candidate editing.

Applicants select from the currently open positions and get an isolated private
draft, scoped session and personal resume link. Candidates are created exactly
once when that draft is submitted. Viewing a reusable link does not create a
candidate. Existing records are not merged by unverified email. Public
pre-onboarding verifies email before personal-data entry; HR verification still
checks interview eligibility. No employee, account or payroll creation occurs.

Source: https://www.rejlers.com/ae/careers/. Its public React widget reads the
AE WordPress `rls_openposition` feed. Sync reads all pages using ID, title,
location and link. HTML/entity normalization is text-only; no source scripts or
job instructions execute. Department is not inferred from the location category.

Sync upserts by unique source ID, preserving manual records and assigned
departments. It reopens returned jobs and closes missing website-owned jobs
only after validating a complete snapshot. Failures preserve the last good data.
A database lease prevents overlap. Default hourly Celery task:
`hr_core.sync_rejlers_positions`; initial/manual command:
`python manage.py sync_rejlers_positions --skip-checks`.

Configuration: `RECRUITMENT_CAREERS_SYNC_ENABLED=true`,
`RECRUITMENT_CAREERS_SYNC_INTERVAL_SECONDS=3600` (minimum 300),
`RECRUITMENT_PUBLIC_LINK_DAYS=30` (bounded 1-365). Existing
`RECRUITMENT_FRONTEND_URL` controls generated link origin. External applicants
need an externally reachable deployed frontend/API; localhost links are local.

Additive migration 0019 adds source/location metadata, sync state and reusable
application links. Private form drafts may have no candidate until submission,
and link to their parent application link. Existing private-invitation APIs,
revision checks, immutable versions, correction, verification, export and
parent/private expiry/revocation checks remain effective.

New routes:

- GET/POST `/api/v1/hr/recruitment-position-sync/`: status/manual synchronization.
- GET/POST `/api/v1/hr/recruitment-application-links/`: list/create; optional
  opening, no candidate required. GET `?link=id` lists submitted applicants.
- POST `/api/v1/hr/recruitment-application-links/{id}/revoke/`: revoke parent.
- POST `/api/v1/candidate-forms/apply/`: read link-scoped openings/register
  applicant draft. Shared credential uses `#apply=`; private resume uses `#invite=`.

HR role and existing action grants remain required. The public route retains
throttling, no-store handling and hashed credentials. Public pre-onboarding
requests a code through the existing private-access route. Shared tokens never
authorize viewing another applicant or editing an existing person's HR record.

Validation: 41 backend/approval tests, 17 browser cases and scoped ESLint pass.
The browser cases cover the simplified creation/copy flow and both public
applicant paths. Migration drift is clean. Backed up local PostgreSQL before
migration to `artifacts/hr-careers-before-20261022.dump`. No real candidate emails
are sent during implementation or verification.

Live local result: 12 open vacancies imported successfully. Worker task is
registered with the 3600-second schedule. The simplified dialog's separate HR
submission-review browser check also passes. Candidate register and sidebar
behavior remain unchanged; no example candidates were seeded.

Tokenless `/candidate-forms` now displays the actual blank CV template. Use
`?form=cv` or `?form=pre_onboarding` to view either form directly. These views
reuse canonical schemas and the candidate field renderer; they cannot save or
submit without an invitation session. GET
`/api/v1/candidate-forms/templates/{cv|pre_onboarding}/` is anonymous, throttled,
no-store and returns blank schema only. Existing authenticated schema APIs and
private access remain unchanged. Pending link credentials are retained only in
tab session storage until session exchange, allowing refresh during applicant
registration and pre-onboarding email verification after fragment removal.

HR-REC-05 supersedes the public Pre-Onboarding registration gate. Both public
form kinds now return isolated draft sessions directly from POST apply/ without
name/email/position inputs. Pre-Onboarding displays all applicable sections on
one page. Session POST actions send_code and verify_email confirm the contact
email at review; final submission requires its match to verified_email. Codes
retain expiry, cooldown and attempt limits. Migration 0022 retains proof for
previously verified sessions. Existing private invitations with candidate data
still verify before access; legacy register=true clients remain compatible.


HR-REC-03 follow-up, 22 October: CV public links open a blank isolated draft
immediately, with no Position, Email address or Full name registration screen.
The actual CV retains its identity/contact fields and shows all sections on one
page before review/submission. A general CV may have no opening; never invent or
choose a vacancy for the applicant. Existing position-scoped links retain their
scope and closure checks. Legacy explicit registration remains compatible.
Pre-onboarding retains registration/email verification. All newly created public
and private invitations expire exactly three days after creation; existing
longer deadlines are capped by migration 0020 at creation plus three days,
without extending earlier deadlines. Applicant drafts inherit their parent
link deadline. Expiry is enforced server-side and displayed in the form/link
dialog. Blank template viewing has no invitation and no submission capability.
