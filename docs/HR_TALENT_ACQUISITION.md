# HR-TA-01 - Talent Acquisition register

User-authorized screenshot page, 21 October 2026. Shared workspace source:
`../docs/features/hr-talent-acquisition.md` relative to the backend root.

Frontend `/hr/talent-acquisition` is accessible through the HR dashboard's
main-content toolbar. Sidebar, shared Header, sidebar hook and layout
configuration are unchanged. The main-content route omits the shared footer
and additional top padding just like the existing HR dashboard.

Subsequent user authorization adds `4.6 Talent Acquisition` to the existing HR
sidebar menu with the hr_management grant. Only that navigation entry changes;
the sidebar's styling and responsive/accordion behavior remain unchanged.

`apps/hr_core/recruitment.py` owns HR-only paginated candidate/opening APIs.
The registered global `hr_management` action guards remain effective. Candidate
and opening writes are atomic with append-only HR audit events. Server-owned
references are CAN + database ID; activity labels do not claim email delivery or
interview completion. No candidate seeds, accounts, employees or payroll rows
are created by the additive migration `0017_recruitment_register`.

Candidates support name, email, position, department, status, phone, nationality,
education, experience, skills and interview notes. Supported stages are Invited,
Applied, Interview, Selected, Pending docs and Rejected. Stages are HR-entered
register values, not proof that an invitation was sent or an offer was approved.
Openings can be added, closed and reopened by selecting the Open Jobs card.
Metrics count all loaded register records; table filters apply to the candidate
list. Refresh preserves existing data on error and identifies the stale display.

New/edit forms persist through the API. Search, filters, pagination, column
visibility, candidate selection and responsive details operate in the browser.
Invitation Link and Convert to Employee open explicit pending-integration
dialogs. Documents show an empty state; no insecure file URLs or fabricated
downloads. Full invitation/form, private-document and verified conversion flows
remain the separately proposed HR-REC-01 plan.

Validation: isolated backend recruitment suite; Playwright desktop/mobile suite;
scoped ESLint; protected shell file hash comparison. Test fixtures contain sample
people solely for browser screenshots; they are never seeded into the app DB.

Local results: 6 backend and 6 browser cases passed, ESLint zero errors
(8 existing App warnings), migration drift clean, protected shell hashes equal.
Migration 0017 was applied to local PostgreSQL after a custom-format backup at
`artifacts/hr-talent-before-20261021.dump` in the workspace. Backend restarted
to register the additive routes. No deployment or invitation sending occurred.

22 October follow-up: HR-REC-02 implements the candidate forms, invitation links,
explicit email sending, submission review/corrections and DOCX export described
in [HR_CANDIDATE_ONLINE_FORMS.md](HR_CANDIDATE_ONLINE_FORMS.md). Earlier pending
integration statements above describe the initial page delivery. Employee
conversion and document uploads remain separate.
