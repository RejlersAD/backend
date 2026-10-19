# Go / No-Go presentation terminology (SAL-TERM-01)

User-authorized display-only rename: Bid to Go, Conditional Bid to Conditional Go,
and No Bid/No-Bid to No-Go. Keep all decision codes, schema, model choices,
API keys/routes, permissions, transitions and integrations intact. No migrations.

Update validation prose, dashboard descriptions, AI wording instructions,
CSV decision/stage labels, letter contexts and PDF/DOCX letterhead labels.
Keep exact source excerpts and stored evidence unchanged. The justification
validator compares the new prose with the equivalent legacy wording using the
existing contradiction patterns; no approval or evidence rule is relaxed.

Related frontend scope/evidence: docs/SALES_GO_NO_GO_TERMINOLOGY.md in frontend.
Verification covers all three decision codes and denial/invalid-output handling,
exports, workflow/proposal regressions and UI submission/accessibility journeys.

Backend regression result: 71 cases, 69 passed, one PostgreSQL-only skip and
one existing scope-expectation failure. The failing foreign-owner/client test
also fails with the previous justification module loaded; `visible_deals`
currently permits module-visible Sales opportunities. Preserve that behavior in
this display-only change. Baseline proof and final run are in frontend
`artifacts/go-baseline-scope.log` and `artifacts/go-terminology-backend-verified.log`.
The executable structure of workflow, preparation, readiness and access modules
is unchanged after excluding display strings (`artifacts/go-contract-check.log`).

Push preparation: merge fetched development/main updates without conflicts,
including the concurrent upstream Equipment List/project-organizer work. Remove
the orphaned marker line in `.gitignore` while preserving its rules. Local normal
`manage.py migrate --check --noinput` exits 0: `radai_dev` has 629 applied records,
including all 32 Sales migrations through `0032_letter_fingerprints`. This patch
introduces no schema changes. Production database state was not queried.

Frontend release checks add 36 passing helper tests and seven passing Analysis
journeys. Existing dialog and Analysis contrast failures are disclosed in the
frontend note; no application styling or access policy is changed by this patch.
