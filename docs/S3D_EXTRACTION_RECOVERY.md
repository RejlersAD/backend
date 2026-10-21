# S3D empty-extraction recovery - 20 October 2026

Authority: the user's follow-up request, recorded in
`../../docs/features/s3d-extraction-recovery.md`. No schema migration is needed.

The installed Anthropic Python SDK 1.12.1 rejects the removed `temperature`
Messages argument. S3D now omits it, preserving the selected model, credentials,
image request and existing provider routing. No SDK downgrade, credential change
or automatic switch to another provider is introduced.

The task counts persisted components after quality checks/persistence. Runs with
no persisted components finish as `failed`, with a safe recovery message and
failure progress. Detected class headers remain reviewable. Existing usage/cost
fields and temporary-key cleanup are retained. The processing report additionally
records `classes_saved`, `components_saved`, `empty_result` and validation status.
The phase label no longer presents a heuristic accuracy estimate as measured
engineering accuracy. Successful component-bearing runs keep the existing flow.

Job `extraction_completeness` responses retain their existing fields and may report
`state: empty` for historical completed jobs without components, or for a newly
recorded empty result. Failed-chunk coverage retains `partial` precedence. This
does not rewrite historical status or synthesize rows. Controlled release still
requires complete coverage and approved consumed matches.
Successful-run completeness dictionaries retain their existing shape so this
UI diagnostic does not introduce a new field into frozen-release input hashes.

Legacy SHA upload deduplication skips empty/partial completed attempts. The
existing automatic-project upload already creates a fresh document/job attempt.
Use Upload document, select the original source and choose Extract database to
retry. Previous attempts and valid rows remain in history. Saved project BYOK
settings/default credentials remain authoritative; expired/rejected credentials
must be corrected in the existing settings workflow before retrying.

The extracted-record queue excludes unbound template rows. Named workbook
sections/full grid still expose templates. Historical empty runs show a warning
and recovery guidance rather than a green extraction-complete badge.

Verification uses `config.settings_release_test` with explicit testing/in-memory
SQLite/no-S3 settings and mocked provider operations. SDK compatibility tests
bind to the installed Messages signature without opening a network connection.
No real AI run, measured extraction accuracy, business approval, production
deployment or migration follows from these tests. The feature brief records
final counts, local process activation and artifacts.
