# PR upload source signatory review

The PR PDF upload accepts optional reviewed **Level** labels and one Additional
Approver. These describe the source PDF review. They do not configure the live
approval route, grant approval authority, or complete a missing canonical
signature. Existing original OCR rows and signed-off evidence remain separate.

## Request and response

`POST /api/v1/procurement/requisitions/import-signed-pdf/` accepts these optional
multipart fields, each encoded as a JSON object:

- `source_approval_review`: the reviewed labels and optional additional signer.
- `expected_source_approval_review`: the prior normalized review returned by
  preview or save. Required whenever `source_approval_review` is supplied.

The normalized empty review is:

```json
{"approval_labels": {}, "additional_approver": null}
```

`approval_labels` may contain only `pm`, `moe`, `mop`, and `vp`. Values are
trimmed text of at most 20 characters, matching the existing presentation-only
`approval_label` convention. Blank values are omitted. No labels are generated
from row order, business roles, or the live workflow's numeric `level`.

`additional_approver` is null or an object with only:

- `name`: trimmed source signer text, at most 200 characters.
- `approval_label`: trimmed text, at most 20 characters; may be blank.
- `signature_verified`: an explicit JSON Boolean for a populated row. This
  records the reviewer's source observation, not a RADAI approval decision.
- `special_note`: optional trimmed text, at most 2,000 characters. Changing
  an already recorded Additional Approver name or Level requires this note.

The review may also include `approver_notes`, a map of `pm`, `moe`, `mop` and
`vp` to trimmed Special notes of at most 2,000 characters. Blank notes and an
empty map are omitted from the normalized response for compatibility. A
captured unknown approver's name or Level correction requires its role note.
Unknown includes empty names, Unknown labels, Not detected/recorded/known/
available, unreadable/unidentified, NA, none/null and numeric-only placeholders.

An empty row normalizes to null. Any entered label or confirmed signature needs
a nonempty name. A populated row requires an explicit Boolean verification
value; string values such as `"true"` are rejected. Unknown keys, unknown roles,
incorrect types and overlength values return HTTP 400. An expected snapshot
without reviewed values is also rejected.
Existing-record submissions that include these review fields require the
existing Purchase Requisition update grant, including standalone attachments.

PR preview, save, detail serialization and paired PR/PO replay responses return
the normalized object as top-level `source_approval_review`. Preview exposes a saved review only when
the uploaded PDF SHA-256 matches both the current source verification and the
review envelope. Original `approval_detection` rows retain their existing
meaning and shape.

These responses also return `default_level_zero_approver`: null when no unique
active canonical Richa identity resolves, or an object with `id`, `full_name`,
`email`, `job_title`, `level: 0`, `status: "not_recorded"` and
`source: "canonical_employee_reference"`. The canonical directory name must be
Richa Hannah Thomas or Richa Thomas; the historical `richa@rejlers.ae` identity
disambiguates matching active records. This reference does not claim that Richa
signed this document. The UI can show the known name without an ID when null.
Source display labels for Richa, Richa Thomas and Richa Hannah Thomas normalize
to `"0"`. Existing historical signature/status data is never rewritten merely
to supply this display label.

## Saved-source correction commands

`POST /api/v1/procurement/requisitions/{id}/source-approvals/` retains its existing
original-document digest, expected row, timestamp, owner/update and source
storage checks. It now accepts an optional `approval_label` (20 characters) and
requires `special_note` (nonblank, at most 2,000 characters). It can correct a
placeholder name even when that source row already has a signature; genuinely
completed, named rows remain protected. A name/Level edit does not infer a new
signature or date. Original detector rows remain captured, while the corrected
source row, matching display annotation and audit are committed atomically.
Existing explicit signature-verification and source-completion rules remain.

Signed-off PDF upload likewise permits a strictly unknown-name correction using
the existing role name input plus its `approver_notes` entry. Known completed
names remain protected. It records the before/after name, note, actor, time and
source digest while retaining the detected signature/date evidence.

`POST /api/v1/procurement/requisitions/{id}/source-review/` edits the saved display
annotations without reuploading or changing commercial data. Its exact JSON
payload contains `document_sha256`, `expected_updated_at`,
`source_approval_review` and `expected_source_approval_review`. It requires the
existing PR update grant and issuer/admin object authority. A change checks the
locked timestamp and reads/hashes the retained original source before saving.
The response is the full PR serializer. Unknown Level changes require a role
note here too. An identical retry does not reread storage or add an audit entry;
its timestamp must still be well formed, even when stale. Invalid/missing tokens
return 400; contested versions or digests return 409; source storage failures
retain the existing 503 behavior. This is an UPDATE RBAC action.

## Comma-separated project references

Native PR `project` writes and new-PR import `manual_overrides.project_number`
accept comma-separated references. Values are trimmed and deduplicated without
changing order; the normalized CSV must fit the existing 200-character field.
No input is silently truncated. `project_numbers` is a read-only array returned
by the PR serializer, preview, save and cached paired responses. Existing-record
preview returns the saved references separately from the original OCR fields,
plus `requisition_updated_at` for deliberate edits.

An attachment updates its project references only when optional multipart JSON
`reviewed_project_references` is supplied as
`{"project_number": "PRJ-001, PRJ-002", "expected_updated_at": "<preview timestamp>"}`.
The update grant, PR lock, freshness check and source identity guards apply.
This updates only project references, structured project details and exact
canonical project resolution; other attachment commercial fields stay protected.
An identical retry permits a valid stale timestamp without another audit entry.
Changes append server actor/time, document digest and before/after references to
protected `price_remarks_data.project_reference_reviews`. The original OCR
snapshot remains unchanged; the approved project snapshot reflects the review.

Matching structured project metadata/IDs are retained, omitted explicit codes
are removed, and new codes use `{type: "project", project_number: code, value:
code}` without invented IDs or names. Internal/name-only details remain. Native
updates derive details only for an actual CSV change and recompute against the
locked record, preserving previously reconciled canonical links on unchanged
or concurrent ordinary edits. Multiple references do not fabricate a single
canonical project identity.

## Persistence and concurrency

The existing import transaction checks the expected normalized snapshot under
the PR row lock, before record or file mutations. A mismatch returns HTTP 409
with `code: stale_source_approval_review` and an actionable `error`. The caller
must retain its input and explicitly preview again before reconciling changes.
If the requested review already equals the current review, an identical retry
passes this annotation precondition without another annotation audit entry,
even if the expected snapshot describes the prior version. Existing import
identity and create/attach guards still apply.

Cached paired imports also check this snapshot. If a subsequent standalone
review restored the earlier expected values, retrying the pair can apply the
requested annotation again under the same lock. That mutation requires PR
update permission, records one audit entry, and preserves the cached pair's
other source evidence and domain state.

Omitting both fields retains a review for the same PDF bytes. Different source
bytes never inherit an active review. Prior annotations remain in the audit
history with their original source digests; replacement records the source
transition and starts with the empty review unless new values were supplied.

Storage is an envelope at
`price_remarks_data.signed_approval_evidence.source_approval_review` containing
`review`, `document_sha256`, `reviewed_by_id`, `reviewed_at`, and `history`.
History entries contain before/after reviews, current/previous document digests,
and server-recorded reviewer/time. A no-op same-source save preserves the exact
envelope. An empty review with no prior envelope needs no annotation record.

The parent `signed_approval_evidence` key is already protected from ordinary
serializer writes and archived by rejected-PR reopening. Additional signer
data never enters detector `approval_rows`, canonical signer foreign keys,
`signed_document_verification.source_approval_rows`, `approval_workflow_config`,
`current_approval_step`, approval status, or notification routing. Existing
attachment identity, commercial-field protection, permissions, signed-source
guards and paired-import atomicity remain in effect.

This adds no model field or migration. The snapshot protects this annotation
review only; it is not a universal PR content revision token.

## Verification

Regression coverage is in
`apps/procurement/tests/test_pr_source_approval_review.py` and
`apps/procurement/tests/test_pr_review_followups.py`, using isolated
`config.settings_release_test` settings, synthetic source bytes and test
storage. It covers validation, round trips, protected attachment/signed-off
behavior, stale rejection, retry audit deduplication and source isolation.

The initial annotation implementation was verified on 25 September 2026 with the explicit test environment in
`../CONTRIBUTING.md`:

- All 27 source-review tests passed (13.998 seconds of test execution).
- All 98 existing tests passed across `test_signed_pr_pdf_creation`,
  `test_signed_pr_duplicate_review`, `test_paired_signed_import`,
  `tests_signed_pr_approval_evidence` and `test_requisition_source_approval_edits`.
- The 21 existing paired-import tests were rerun after the final cached retry
  change and passed. These are included in the 98, not additional distinct cases.
- Changed Python files compile and the scoped Git whitespace check passes.

Run the new suite from `backend/` with
`..\.venv\Scripts\python.exe manage.py test apps.procurement.tests.test_pr_source_approval_review --settings=config.settings_release_test --noinput`.
These 125 distinct functional SQLite cases do not certify PostgreSQL lock races
or live OCR. No model or migration changed, and no live data was written.

The subsequent saved-detail/unknown-correction/project-reference changes were
verified on the same date:

- A combined 206-case run passed in 81.742 seconds across the follow-up and
  source-review suites plus `test_requisition_source_approval_edits`,
  `test_signed_pr_pdf_import`, `test_signed_pr_pdf_creation`,
  `test_paired_signed_import`, `test_requisition_project_retention`,
  `test_requisition_conversion_service` and `test_purchase_order_exports`.
- After the last legacy Richa display compatibility change, all 79 affected
  source-review/correction cases passed in 20.717 seconds. This adds one new
  regression to the earlier run: **207 distinct cases** across these checks,
  including 25 dedicated follow-up cases. Repeated cases are not extra coverage.
- Final changed Python sources compile; the scoped Git whitespace check passes.

Local test logs are `.codex_tmp/pr-review-followups-final-verified.log` and
`.codex_tmp/pr-review-followups-final-projection.log`. They are ignored artifacts,
not application data. Commands use the `CONTRIBUTING.md` SQLite/in-memory test
environment and `--settings=config.settings_release_test --noinput`. These
checks validate domain/API behavior, permissions, retry/stale/failure paths and
PDF/Word display regressions. They do not certify concurrent PostgreSQL lock
behavior, production deployment, live OCR, or real-user document changes.
