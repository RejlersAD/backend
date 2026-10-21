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
