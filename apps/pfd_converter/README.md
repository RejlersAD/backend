# PFD output integrity — F05

Implemented locally on 24 September 2026. This contract concerns existing `PIDConversion` artifacts and review evidence; it does not define engineering approval policy or introduce a document platform.

## Reads and commands

- `GET /api/v1/pfd/conversions/{uuid}/download_drawing/` reads existing stored bytes. It never generates, deletes, saves, revises or approves a conversion. History and enhanced PDF download endpoints use the same read service. The history PID route accepts the actual UUID model identity.
- Downloads retain the existing `pfd_to_pid.export` guard and existing record visibility. A supplied single `force_regenerate=false`, `0`, `no` or `off` is accepted as read-only. Enabled, malformed, empty or repeated regeneration flags return HTTP 400 directing clients to the separate command.
- `POST /api/v1/pfd/conversions/{uuid}/regenerate/` uses the existing registered `regenerate` action (`pfd_to_pid.update`) and scoped `get_object()`. Download permission alone does not authorize it. No grants or business approval configuration were changed.
- Regenerate requires both `expected_updated_at` (the exact server timestamp) and `expected_artifact_sha256` (the exact output fingerprint). Missing/malformed tokens return 400; stale records, changed bytes or a stored fingerprint mismatch return 409. Missing stored output returns 404; failed generation, storage or persistence returns 503. None of these outcomes publishes replacement evidence.
- Successful regeneration returns HTTP 201 with a **new conversion UUID**, separate storage object and `completed` execution status. Its review is **unreviewed**: no reviewer, review time, review notes, confidence or compliance result is copied. The original conversion and artifact remain unchanged. The existing engineering revision label is retained, since no new company revision numbering rule was approved; UUID plus lineage identifies the distinct output.

The command copies the existing stored equipment/instrument/piping/safety/design basis, valid stored valve JSON and parent project header values into a detached snapshot. Empty equipment specifications, unsupported lifecycle states, or malformed stored list shapes make regeneration unavailable. The existing local `GraphBasedPIDGenerator` renders the supported specification fields into a unique temporary PDF. The wrapper which adds engineering defaults and provider extraction are not called. This is a local re-render, not a new AI conversion, technical validation, or assurance that every engineering detail in an earlier drawing can be reproduced. Its technical limitations require engineering review. A necessary logger initialization fix makes the existing renderer importable when its optional local Azure algorithm package is absent.

After generation and storage read-back validation, the command locks/reloads the source, checks the timestamp, all concrete conversion fields, actual source bytes and parent project header basis, then creates the child. Earlier approvals and concurrent edits cannot be overwritten by this command. Multiple independently authorized runs can create distinct children; there is no claim of request deduplication or a single latest revision pointer.

## Artifact and approval evidence

Conversion detail/history responses include:

```json
{
  "updated_at": "exact server timestamp",
  "artifact": {
    "identity": "conversion UUID",
    "sha256": "SHA-256 of the actual stored bytes, or null",
    "available": true,
    "review_state": "unreviewed",
    "source_conversion_id": "parent conversion UUID, or null"
  },
  "allowed_actions": ["download", "regenerate"]
}
```

Availability and allowed actions are checked against real bytes, recorded fingerprints, supported inputs and current module grants. `review_state` can also be `approved`, `legacy_approved`, `unavailable` or `integrity_mismatch`. Historical approved records without exact-output hash binding retain all their original status/reviewer/date/notes, but are explicitly `legacy_approved`; hashing today's bytes does not retroactively prove what a historical reviewer saw.

`POST .../{uuid}/approve/` retains the existing configured business-position/assignment route. It requires the same exact timestamp and fingerprint and only accepts completed, unreviewed output. It records the approved SHA-256 under the existing `conversion_data._radai_artifact_v1` namespace in the same transaction as the decision. Repeated or stale approvals cannot rewrite prior review evidence. No configured approval route remains denial, including for administrators. Download detects a later stored hash mismatch and refuses the mismatched artifact.

Generated output, source relationships, state and review evidence cannot be supplied through generic create/update APIs. Metadata edits are allowed only before there is stored output/review, with a locked recheck. Existing feedback remains a separate record. Initial ultra and intelligent generation filenames now include conversion UUIDs so repeating a drawing number cannot overwrite an earlier conversion's stored file.

## Failure and verification boundaries

Generation uses a temporary directory. New stored candidates are verified before publication; generation, storage/read-back and child insertion failure retain source evidence and clean up only the command's uniquely named candidate where possible. File storage and the database are not one atomic system: storage failure after a write, cleanup failure, process interruption, or a failure at the enclosing route transaction commit can leave an **unpublished orphan candidate**. No previous artifact is deleted to recover it. No worker, cleanup job, retention policy or infrastructure changes were added.

The application does not establish storage-level immutability against outside writers. Stored hashes detect subsequent changes for bound outputs; old unbound approvals cannot prove historical bytes. Broader record scope and business approval policy remain the existing implementation and unresolved D-02/D-03/D-04/D-08 decisions.

Focused synthetic guarded API checks are in `tests.py`, with temporary local storage and denied requests/httpx network calls. Run from `backend/`:

```powershell
$env:PYTHONIOENCODING='utf-8'
$env:DATABASE_URL='sqlite:///:memory:'
$env:AIFLOW_ENVIRONMENT='testing'
$env:ENVIRONMENT='testing'
$env:USE_S3='false'
..\.venv\Scripts\python.exe manage.py test apps.pfd_converter.tests --settings=config.settings_pfd_integrity_test --noinput --verbosity 1
```

Final local backend verification: **33 tests passed** in 6.616 seconds with exit 0, including two same-number initial ultra/intelligent generation runs preserving the first run's synthetic approval and distinct original bytes, plus failure after child insertion. Changed Python files compile and the scoped Git whitespace check passes. Requests/httpx network-block assertions passed. Additional UI/build results are recorded in the workspace [F05 brief](../../../docs/features/pfd-output-integrity.md) and [audit](../../../docs/DESIGN_INTENT_AUDIT.md). The SQLite/model-sync harness exercises the actual production views, route guard, permissions and configured approval eligibility with synthetic data. Mid-generation/storage edits are deterministic interleavings within the test connection; they are not certification of PostgreSQL concurrent locks. The real local renderer smoke uses no rendering mock.

F05 adds no schema migration. The existing `0002_pfddocument_uploaded_file_and_more` migration defines `assumptions_report`, `completed_at`, `conversion_data`, `conversion_method`, `pid_pdf` and `valve_list`. Release review on 24 September 2026 corrected the earlier claim that these fields were missing from migration history. The SQLite model-sync tests do not establish whether that migration is applied to a particular database; database migration verification is separate. The original F05 implementation performed no live provider calls, pushes, PRs, merges, deployment or infrastructure changes.
