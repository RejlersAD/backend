# Bid preparation migration verification

Sales migration `0015_bid_preparation`, verified on 1 October 2026 using a
task-owned PostgreSQL 15 container with temporary storage and loopback port 15448.
No production database or business record was used.

`scripts/check_bid_preparation_migration.py` executed the actual additive
migration operations, with the preceding schema reconstructed from synchronized
models. This is not a fresh replay of all historical migrations.

Passed checks:

- Empty reverse and forward operations execute successfully.
- Every existing field on ten synthetic source model types keeps its original
  hash, including authored content, decimal money, submitted/approved state,
  technical snapshots and resource rates.
- The three preparation tables start empty; no source matching or backfill.
- PostgreSQL exposes the declared foreign keys and uniqueness constraints and
  rejects a duplicate actor/request identity and nonexistent technical source.
- Reversal after creating a connection/capture/command refuses removal and keeps
  source facts and all preparation evidence intact.
- Django migration-state check for Sales and Planning reports no changes.
- Four real contention tests observe a waiting PostgreSQL lock, then verify
  connection/capture retries have one effect, competing capture loses with409,
  and a concurrent technical writer completes its audit without deadlocking
  while the stale capture preserves original Quote content.
- Final40-case preparation API suite and four deletion/correction-history cases
  also pass on PostgreSQL. Logs: `bid-preparation-postgresql-final.log` and
  `bid-preparation-delete-postgresql.log` under `artifacts/`.

Evidence: `artifacts/bid-preparation-migration.log`,
`artifacts/bid-preparation-migration-state.log` and
`artifacts/bid-preparation-postgresql-tests.log`. The checker requires explicit
synthetic credentials, a nondefault loopback port and a dedicated empty database.
Retain the additive schema on application rollback once evidence exists; never
delete captured history to bypass the reversal guard.

Local runtime application and final results are recorded in the workspace
[feature brief](../../docs/features/bid-proposal-preparation.md). Production
migration/deployment and full historical-chain verification remain separate.
The verified migration is applied to local `radai_dev`; no preparation data was
backfilled. Anonymous local API checks deny access. Existing-record Quote smoke
was skipped because that local database has no Quotes. The synthetic verification
container was removed after all checks completed.
