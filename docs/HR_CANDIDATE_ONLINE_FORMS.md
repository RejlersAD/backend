# HR-REC-02 - Candidate online forms

Authorized 22 October 2026. Both Word templates are mapped in
`apps/hr_core/recruitment_form_schema.py`. Illustrative CV text is never used as
candidate defaults. Company-template beneficiary wording remains form content.

In 4.6 Talent Acquisition select **Invitation Link**. Choose a candidate, open
position and form. HR can preview fields, record an interview outcome, create a
private link, copy/open it or explicitly email it. A replacement revokes the old
link/session for that candidate, position and form. Closing a position blocks
candidate access. Pre-onboarding requires a suitable interview outcome and an
emailed one-time code before session creation.

Candidate `/candidate-forms#invite=...` removes the fragment immediately and
exchanges it by POST. No employee account is needed. Server drafts support
repeatable education, languages, projects, employment, relatives and dependants.
Review precedes final submission. Stale draft reload requires acknowledgement
of unsaved changes. Submitted versions are immutable; HR correction reopens
the draft while retaining prior versions.

HR views versions, downloads DOCX, requests corrections and verifies the latest
version. Verification requires HR approve permission; download requires export;
other actions preserve module grants and HR-only access. Candidate receipts
support browser print/save as PDF. Typed names record acknowledgement, not a
verified digital signature. No employee creation, payroll enrollment or uploads.

## Additive API contracts

- GET/POST `/api/v1/hr/recruitment-forms/`: list by candidate/create invitation.
- GET `/api/v1/hr/recruitment-forms/schema/{cv|pre_onboarding}/`: field preview.
- POST `/api/v1/hr/recruitment-forms/{id}/command/`: explicit send/revoke.
- GET/POST `/api/v1/hr/recruitment-forms/{id}/review/`: versions; correction/verify.
- GET `/api/v1/hr/recruitment-forms/{id}/export/{version}/`: private DOCX.
- POST `/api/v1/candidate-forms/access/`: credential exchange and pre-onboarding
  emailed-code request/verification.
- GET/POST `/api/v1/candidate-forms/session/`: scoped schema/draft/receipt and
  revision-checked save/submit using a separate bearer session.

Responses are private/no-store with no-referrer handling. Store only invitation,
one-hour session and code hashes. Codes expire after ten minutes, with five
attempts and resend cooldown. Public endpoints are throttled. Tokens and personal
form values are omitted from audit metadata. Unknown fields, excessive rows/text,
invalid dates/emails and missing final acknowledgement are rejected server-side.

Migration 0018 adds interview outcome, invitation/draft state and immutable
submitted versions without seeds. Evidence remains HR-visible after revocation
or expiry. Submission notifies the HR owner internally without family details.

Configure `RECRUITMENT_FRONTEND_URL` (defaults to FRONTEND_URL),
`RECRUITMENT_INVITATION_DAYS` (default seven, bounded 1-30) and the existing
Django email transport/DEFAULT_FROM_EMAIL. SMTP must work for invitations and
pre-onboarding verification codes. Persist the sending attempt before SMTP;
repeated successful send commands do not resend. Failure allows explicit retry.
A process interrupted in sending is not automatically retried: acceptance may
be uncertain. Create a replacement link for a new explicit attempt. Transport
acceptance is not proof of inbox delivery.

Verification uses synthetic candidates with locmem or mocked email. No real
messages are sent. Retention policy, private-file storage/scanning, reminders
and employee conversion remain separate integrations.

Local verification: 18 recruitment/backend tests and 13 Chrome browser cases
passed. New recruitment files lint clean; shared App retains 8 existing
warnings. Schema/migration drift check passed. Migration 0018 applied after
backup `artifacts/hr-forms-before-20261022.dump`; no source data or seeds loaded.

Combined recruitment and existing HR business-approval suite: 31 tests pass
using `config.settings_candidate_forms_test` (includes lifecycle relations).
Both forms also pass the actual application-route/public-layout smoke check
with their full schemas and zero browser errors. Local routes return HTTP 200.

HR-REC-03 supersedes the original UI's candidate-selection requirement. HR now
chooses only form type, creates and copies the link. Applicants choose a current
opening and create their candidate record on submission. See
[HR_CAREERS_PUBLIC_LINKS.md](HR_CAREERS_PUBLIC_LINKS.md). Private APIs remain
compatible; public pre-onboarding retains email verification and HR review.
