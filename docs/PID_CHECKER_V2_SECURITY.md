# P&ID Checker V2 API security — Task 1

Authority: the user's 12 November 2026 Task 1 request; bounded by the shared
`docs/DECISIONS.md` I-07/D-03 access audit and repository gap report.

The registered `/api/v1/pid-checker-v2/` routes use the existing
`pid_analysis` action guard. Custom read, edit, upload, delete, and export
operations have explicit action classifications. The P&ID scope permission
checks selected project and direct record/document IDs before execution.
Organizer project access follows its creator or active enterprise project
membership/ownership; PIDVProject and the isolated Checker V2 project have
creator ownership because they lack a member relation. Existing per-user
record filters continue to apply. Cross-project symbol fallback uses only
other projects owned by the requester; default symbols remain available.

`GET /api/v1/pid-checker-v2/extract-instrument-tags/status/{job_id}/` returns
the existing cached payload only to the recorded job owner with
`pid_analysis.read`. Missing, expired, or ownerless jobs return 404. The
current instrument extraction POST still runs synchronously; this does not
change its dispatch or response. Chat archive keys now include the current
user namespace because its project label is free text.

Verification: `apps.pid_checker_v2.tests.test_security` under isolated
`config.settings_pid_checker_security_test`; shared action-enforcement tests
under `config.settings_permissions_test`. No migration or production data
change is involved.
