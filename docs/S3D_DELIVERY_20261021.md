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
