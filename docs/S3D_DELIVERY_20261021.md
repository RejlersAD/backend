# S3D reference, release and BUHASA delivery — 21 October 2026

Authority: explicit user requests for S3D reference intelligence, controlled
release/pilot, extraction recovery, BUHASA matching and field review; followed
by authorization to align/push Development and create PRs. I-06 requires
incremental reuse. Existing APIs and successful results remain compatible.

Includes additive migrations 0014_reference_intelligence,
0015_controlled_release and 0016_import_pilot; pinned reference integrity and
match decisions; canonical BUHASA review with row identities; controlled
validation/freeze/export/pilot gates; extraction failure recovery; and optional
source provenance saved atomically with approval. No automatic release grants,
reference approval, real import acceptance, or production deployment follows.

Contracts: [reference intelligence](S3D_REFERENCE_INTELLIGENCE.md),
[controlled release](S3D_CONTROLLED_RELEASE.md),
[extraction recovery](S3D_EXTRACTION_RECOVERY.md), and
[BUHASA matching](S3D_BUHASA_CODELISTS.md). The two packaged JSON dictionaries
must remain byte-identical to the frontend-generated assets. Source locators
remain in existing decision JSON; absent metadata preserves legacy snapshot
shape. Real reference-pack and engineering/pilot acceptance remain open gates.

Alignment, migration verification, final checks and PR evidence are recorded
below before publication. Local PostgreSQL verification and disposable replay
are distinct from production migration/deployment evidence.

Verification on 21 October 2026:

- Merged current `origin/development` and the equipment fixes on `origin/main`
  without conflicts, retaining the existing shared configuration changes.
- Isolated S3D suite: 112 tests, OK, with one existing skip. A subsequent
  focused BUHASA suite passes all 11 tests, including newline portability.
- S3D model migration drift check: no changes detected.
- Disposable PostgreSQL: fresh migrate through S3D 0016, reverse 0016–0014
  back to 0013, and reapply all three passed. Disposable database was dropped.
- Local PostgreSQL reference probe passes matching, pinning, ambiguity,
  review/rerun uniqueness and freeze protection; synthetic rows rolled back.
- All six local PostgreSQL controlled-release probes passed, including actual
  reviewed codes in generated workbooks, frozen bytes, override invalidation,
  access controls and pilot reconciliation; synthetic rows rolled back.
- Local `radai_dev` has S3D 0014–0016 applied. Pulled electrical migration
  0009 and PID migrations 0013/0014 were applied successfully. Final migration
  audit: no pending migrations, no conflicts and no missing S3D tables/columns.

The dictionary fingerprint preserves its original Windows value on both LF
and CRLF checkouts. This prevents Git newline normalization from invalidating
existing snapshots; dictionary data and historical hashes were not replaced.
The reference probe now recognizes already-applied 0014 when later migrations
are present, without planning a downgrade of 0015/0016.

## Restored-database migration repair and release follow-up

The supplied deployment trace fails inside 0014 because the physical workbook
ID key is absent, although 0004 declares it. Migration 0014 now invokes the
existing PostgreSQL reference-key preflight before adding fields or models.
It covers MatchingWorkbookSet, PaperSpecExtractionJob and the configured user
model. Healthy primary/unique keys are preserved; missing keys are restored
under transaction-held locks after checking for null and duplicate IDs.
Conflicting identities abort atomically without deleting or renumbering rows.
The migration graph and model/API contracts are unchanged. Already-applied
0014 is not rerun; reversal retains repaired identity keys.

The five new PostgreSQL regression tests execute the entire migration,
including deferred foreign keys, in temporary schemas with rollback. They
pass for missing keys, healthy primary keys, existing unique indexes, repeat
preflight, FK enforcement, and null/duplicate IDs at each of the three targets.
The original missing-key FK failure is reproduced before testing the repair.

Current local verification: all migration history is consistent, the full
registry has no pending migrations, and Spec Customization has no model-state
drift. Direct PostgreSQL inspection confirms applied 0014-0016 and physical
primary/foreign keys. The focused reference-intelligence suite passes 11 tests.
These are local results; the database in the supplied deployment trace has
not been accessed or migrated. The initialization-time DB warning remains
a separate issue.

Both remote branches were fetched explicitly. Development already matched
origin/development; the latest origin/main was then integrated by fast-forward
without conflicts. No force push or main merge is part of this release.

Final backend suite: 118 tests, OK, six skipped (the existing skip plus the
five PostgreSQL-only tests, which passed separately on local PostgreSQL).
The suite uses config.settings_release_test and isolated SQLite; it does not
replace the separate PostgreSQL migration regression and live-schema audit.
git diff --check passes. Logs are in parent workspace artifacts under
release-20261021-{backend-all,key-regression,all-migration-check}.log.
