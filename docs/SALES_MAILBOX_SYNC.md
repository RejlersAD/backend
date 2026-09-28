# Sales email automation: automatic sync (Task 2)

Extends [durable capture](SALES_EMAIL_CAPTURE.md) with scheduled, resumable reads
of existing and new incoming mail. Source snapshots retain the same ownership,
access rules and identity. No mail is sent, marked read, moved or deleted. No
customer, lead or opportunity is automatically created.

## Enable, pause and visibility

`POST /api/v1/sales/mailbox-connections/{id}/configure-sync/` accepts only:

```json
{"enabled": true}
```

Use `false` to pause. The actor needs current `sales_email_intake` read, create
and update actions plus the established connection owner/administrator scope.
Application mode and stored tenant/client IDs are required. The command records
the authorizing actor, fences prior workers and returns `{enabled, sync}`.
It performs no Graph read. Invalid payloads return 400, missing scope 404, and
missing authority 403. Each worker rechecks current actor authority and source
identity before selecting work and committing results. Revocation blocks further
writes. A later authorized enable can resume the same saved checkpoints.

Once sync state exists, generic updates cannot toggle enabled or repoint the
connection identity; deletion and delegated OAuth replacement are protected.
The retained state belongs to this mailbox even before its first saved source.

Mailbox list/detail add a private, read-only `sync` projection:

- `enabled`, `status`: not_configured, paused, queued, running, retrying, blocked,
  or up_to_date.
- `saved_count`, `pending_count`, `failed_count`: actual retained sources and
  queue counts. Failed includes unavailable sources; pending IDs can include
  outgoing/unknown mail awaiting direction checks.
- `last_successful_sync_at`, `initial_sync_complete`, `error_code` (static allowlist).

The existing Sales connection panel displays this separately from connection
health. Refresh makes only the existing list GET. Counts do not claim a total
mailbox size; saved counts are confirmed incoming sources only. Initial import
remains incomplete while discovery, folder backfill or source issues remain.
An unavailable source stays visible without repeatedly fetching its 404; later
delta evidence can requeue it. The last successful time never advances merely
because a worker started or a connection test passed.

## Runtime and delivery

Migrate `sales.0009_automatic_mailbox_sync` before starting the new web/worker
code. Use the existing Celery broker, Linux prefork worker and a single Beat
scheduler. Deployment default is **off**. All three processes must share:

```text
SALES_MAILBOX_SYNC_ENABLED=true
SALES_MAILBOX_SYNC_INTERVAL_SECONDS=60
SALES_MAILBOX_SYNC_MAX_STEPS=30
SALES_MAILBOX_SYNC_WORK_SECONDS=60
```

The non-eager worker/broker and explicit per-connection command are both required.
A legacy `enabled=true` alone does not authorize imports. Docker env-file edits
require container recreation; restarting an existing container does not reload
its environment. Store credentials only through the existing secure server
configuration, never the browser or task arguments.

Beat dispatches due connection UUIDs. Each task claims a 180-second SQL lease
with a fencing token. Connections are locked before sync rows. Network reads
hold no database locks. Work is bounded to at most 30 steps/60 seconds, with
Celery soft/hard limits of 120/150 seconds covering stalled requests. Requests
also use bounded timeouts and streamed response-size caps. Prefork task limits
must not be assumed effective on Windows/solo pools. Dispatch stops after the
first broker failure; SQL due work remains eligible for the next tick.

Mailbox folder delta discovers ordinary accessible folders; per-folder message
delta enumerates existing IDs without a date filter, then subsequent changes.
Each accepted page atomically stores its IDs and advances its private checkpoint.
Source capture and ledger completion commit together. Immutable-ID uniqueness
deduplicates repeated pages and moves within the same mailbox. Remote tombstones
do not remove saved evidence. Cursors are signed and bound to connection/source/
folder, and exact URLs are checked against the configured public Graph resource.
Signing is integrity protection, not encryption; restrict database access.

Retries use durable timestamps and honor Retry-After. Error items do not prevent
other eligible items from progressing, and ID-only delta replay does not shorten
their backoff. Expired checkpoints restart enumeration with existing identity
deduplication. Stale, paused or reclaimed workers cannot commit. An abandoned
lease becomes eligible again after expiry. No durable-delivery claim depends on
an in-process callback or Redis-only lock.

## Recovery and limits

Pause through configure-sync before maintenance. Saved sources, queued work and
checkpoints remain. Restore worker/provider access and re-enable through the
guarded command when required. The global gate stops new dispatch; every worker
also checks its runtime gate. Recreate all processes when changing that gate.

0009 creates only the sync-state, folder and message-work tables. It does not
rewrite mail or mailbox configuration. Before any state exists it reverses
normally; once state exists its reverse guard refuses before DDL, preserving
progress and work. Retain the compatible schema or use a verified backup and
explicit recovery plan. Do not discard state to bypass the guard.

This is not a full archive: hidden/recoverable/archive stores and attachments
are not certified or downloaded. Outgoing, draft and uncertain-direction mail
are not captured. Each saved item contains its own available body/quoted chain;
sync does not independently assemble every sibling into one snapshot. The first
saved version remains immutable. No delta latency guarantee or business retention
policy is introduced. Local machine/worker shutdown pauses progress.

## Verification

On 28 September 2026, the final Python 3.11 suite passed **227** functional and
historical-migration tests. Six separate PostgreSQL tests passed, covering
competing workers, pause during a real unlocked fetch, expired-lease fencing,
and the existing capture lock/revocation cases. Provider test data is synthetic;
unrelated fixture notification delivery is mocked.

Real local PostgreSQL migration 0009 preserved all fields of the existing saved
source and every mailbox setting. Full registry: **561** applied migrations,
**9** Sales migrations, zero pending/conflicts/model drift. Historical 0008→0009
tests separately preserve captured and legacy source rows and prove the reverse
guard runs before schema changes. These are local results, not production proof.

Frontend verification: **18** focused browser cases, accessibility, scoped lint,
desktop/mobile inspection and Node 20 production/PWA build passed. Logs are in
backend `artifacts/sales-sync-*.log` and frontend `artifacts/email-automatic-sync/`.
Live local worker evidence is recorded in the workspace Task 2 feature brief.
Actual local Beat/worker import discovered 371 folders and retained 19 confirmed
incoming sources by the post-restart check, with zero source failures reported.
A controlled pause/restart preserved all checkpoints and queued items; scheduled
capture resumed and is left enabled. Initial backfill is ongoing, not complete.

References: [folder delta](https://learn.microsoft.com/en-us/graph/api/mailfolder-delta?view=graph-rest-1.0),
[message delta](https://learn.microsoft.com/en-us/graph/delta-query-messages),
[immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id),
[Celery task limits](https://docs.celeryq.dev/en/v5.3.4/userguide/workers.html#time-limits).
