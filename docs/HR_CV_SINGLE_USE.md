# HR-CV-ONE-01 — close completed CV links

Authorized 22 October 2026: accept one CV submission per link, add Position
Applied and Nationality to Candidate details, and replace the submitted form
with an acknowledgement offering only Close page.

Context: workspace README/PROJECT_BRIEF, DECISIONS, REPOSITORY_GAP_REPORT,
ARCHITECTURE, DATA_MODEL, API_CONTRACTS, SECURITY, CONTRIBUTING and the existing
HR online forms, Careers/public links and CV-assisted-entry feature briefs.
The earlier reusable-CV-link behavior is superseded by this request.

CV application links become consumed when their first immutable submission
exists. Access and submission serialize on the existing parent link row, so
other visitors and already-open drafts cannot create a second submission.
This also closes previously used CV links. No migration or destructive data
cleanup is needed. Expiry, revocation, permissions and HR review/export remain.
Pre-onboarding links retain their existing behavior. A CV correction needs a
fresh HR invitation; requesting correction cannot reactivate a consumed link.
The original submission remains available to HR as immutable evidence.

Completed CV access/session GET returns only kind/state/closed acknowledgement
metadata, without the submitted personal data or a new session. A consumed
shared link or another live draft returns HTTP 409 with code cv_link_closed.
The browser shows the acknowledgement, removes saved session/resume credentials
and form data, and preserves only a tab-local completion marker for refresh.
New invitation fragments clear that marker. Close page attempts window.close;
if the browser refuses, it navigates to about:blank without reopening the form.

Schema v3 adds optional candidate.position_applied (200 characters) and
candidate.nationality (100 characters), preserving older drafts and callers.
Both appear in Candidate details, review and HR export. An existing opening or
candidate supplies initial values; submitted values update the candidate
register without creating an employee/user. Current position remains separate.

Verification includes existing CV/pre-onboarding regressions, parent/private
link reuse, existing draft rejection, canonical candidate values, field limits,
minimal receipts and browser acknowledgement/refresh/close checks. A dedicated
PostgreSQL test races two independent sessions and asserts one HTTP 200, one
409, and exactly one candidate-bearing invitation/submission. The isolated
settings_candidate_forms_postgresql_test registry reuses the guarded disposable
CI database configuration; it cannot select the application database.

Verified locally: 43 CV/forms/Careers/pre-onboarding regressions passed; all
four single-use checks passed on disposable PostgreSQL, including the real
two-session race. Sixteen browser cases passed (14 existing form flows and two
completed-link cases). Scoped ESLint, frontend dependency-graph bundling and
the HR model/migration drift check passed. No real applicant submissions,
emails or employee creation were used during verification.
