# Sales and shared records release - 1 October 2026

This release connects canonical clients, employees and projects with Sales,
Planning, Finance and Project Control records; adds bid preparation and proposal
review; and extends opportunity documents with preserved originals, revisions,
classification/custom tags and previews. Go opportunities appear in proposal
preparation, and eligible Active or Prospect clients can start proposals. AI
writing remains a reviewable draft. This does not claim that the entire
Lead-to-Closeout roadmap is complete.

The user-approved temporary bid-decision fallback uses current Opportunity module
access when its business approval route is absent. It preserves record scope and
explicit denials and does not bypass a present configured route. This is a scoped
temporary behavior, not a new company-wide approval policy.

## Integration

The actual upstream branch is `development`. Local work was committed there,
pulled from origin/development, and merged with origin/main `cc03c276` without
conflicts. The integration baseline is `a160bdb7`; schema/test fixes are committed
at `1d189261` and the native image dependency correction at `eed77847`. The final
release PR targets `main`. No main merge or
production deployment is included.

## Verification

Detailed commands, results and boundaries are recorded in:

- [Functional, permission and concurrency checks](SALES_LIFECYCLE_RELEASE_TESTS.md).
- [Dependencies, image and migration checks](SALES_LIFECYCLE_RELEASE_MIGRATIONS.md).

The affected-domain SQLite runs completed with 993 Sales cases (6 database-only
skips), 360 adjacent-domain cases (7 database-only skips), and 58 RBAC cases.
PostgreSQL checks cover database-specific behavior separately. Source compilation
and fatal-code lint passed for all 118 changed Python files. The final Python 3.11
image passes dependency/import/native/system/static checks and 76 focused tests
against its freshly resolved dependencies. Publication remains gated on final
Git synchronization and review. PostgreSQL verification passed 415 distinct
cases across the broad run and a 33-case strict run, including all seven invoice
cases initially deferred by the local-host safety guard and 26 required Purchase
Recommendation cases. All 10 CI-runner controls passed. Paired frontend checks
also pass: 510 Node tests, 480 distinct browser cases and the clean production/PWA
build. No required validation gate remains failing.

Broad verification corrected test isolation in the bid audit and Project
Control fixtures, plus one invalid quote literal in the existing OCR parser.
These fixes preserve production authorization and business behavior.

A clean dependency-layer probe found that PaddleOCR's OpenCV contrib wheel needs
the `libGL.so.1` system library. The Dockerfile now installs `libgl1` and verifies
`pip check` plus OpenCV/olefile imports during the build. Corrected dependency
probes passed native OCR/provider/PDF imports and actual OpenCV, Torch and Paddle
array operations. The final image and fresh-dependency smoke results remain
recorded separately in the migration/build report.

## Database and recovery

The explicitly inspected local PostgreSQL database was backed up before applying
15 engineering migrations brought in from main. Actual-schema checks then found
two missing Finance tables and the missing PIDV2 tag index table despite the
older recorded migration state. Finance 0017 and PIDV2 0006 now repair that state
through real migrations. They preserve compatible existing tables and reject
incompatible schema; they do not create business data or approvals.

All 17 migrations applied locally without faking. Final checks confirm consistent
history, zero pending migrations, no model drift, all managed tables/columns
present and no Django system-check issues. Eight disposable PostgreSQL DDL
probes passed, including populated-table preservation and incompatible-schema
rejection. Production migration state has not been inspected or changed.

Apply forward migrations before starting dependent application code. The schema
repairs intentionally refuse reversal to protect existing tables. Other new
identity/document/proposal migrations also protect reviewed evidence. After
compressed uploads exist, any application rollback must retain an encoding-aware
reader or use a separately reviewed, verified decompression migration. See the
migration report for exact scope and recovery boundaries.
