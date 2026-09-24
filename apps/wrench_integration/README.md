# Wrench synchronization outcomes

F04 local correction, 24 September 2026. This documents implemented behavior;
it does not establish an external Wrench contract or resolve the adopted
context's D-05 writable-field authority or D-07 interface/entitlement decisions.
See the workspace [feature brief](../../../docs/features/wrench-sync-outcomes.md)
and [audit](../../../docs/DESIGN_INTENT_AUDIT.md).

## Supported operations and effects

`POST /api/v1/wrench/sync/trigger/` accepts the existing request keys
`direction` and `entity_type`. The two supported public combinations are
`wrench_to_radai` with `document` or `transmittal`. They retrieve a metadata
page using existing adapters and create a synchronization log. They do not
import canonical RADAI records or write records to Wrench.

Pushes, project/user pulls and `all` have no implemented synchronization path.
They return HTTP 400 with `code: sync_unsupported`, `status: unavailable` and a
safe explanation before configuration lookup, external requests or log creation.
The existing internal `run_sync(..., entity_type='doc_search')` alias remains;
the public endpoint continues to reject that alias as an invalid entity type.
No new direction, endpoint, model or migration is introduced.

`GET /api/v1/wrench/config/` retains `configured` and `config`, and adds
`sync_capabilities`: the two supported public combinations with metadata-only
descriptions. `configured: false, config: null` explicitly means no active
configuration. A failed or denied read is not evidence of missing configuration.
Starting a supported operation without active configuration returns HTTP 424.
Existing credential storage and read serialization are unchanged.

## Outcome evidence

An executed attempt returns the existing serialized `WrenchSyncLog` envelope
with HTTP 201. This means a log was created, not that the operation succeeded.
The existing route guard rolls back writes for HTTP error responses; retaining
the 201 envelope for executed failures preserves their log and audit evidence.
Clients must inspect `status`, counters and `sync_details`.

- `success`: the expected metadata collection and count passed validation, with
  `records_synced == records_requested` and `records_failed == 0`. An explicit
  empty collection with zero total is a valid zero-result retrieval.
- `partial`: a nonempty page was retrieved and more records are available.
  `records_failed` remains zero because unfetched records are not failures.
  `remaining_available` states the remainder. No subsequent work is queued.
- `failed`: an adapter, authorization, transport, schema or evidence failure
  prevented confirmed retrieval. Safe errors are returned without copying
  external error payloads into the log. Counter defaults are not verified
  business results when `retrieval_validated` is false.

Every new log has `sync_details.effect: metadata_retrieval`,
`canonical_records_imported: 0`, `retrieval_validated` and `attempts`.
Validated results additionally include `fetched`, `total_available`,
`remaining_available`, `scope: requested_metadata_page`, a descriptive message,
and `external_status_interpreted: false`. External numeric operation codes are
diagnostic only: their business semantics have not been established. Explicit
error flags, missing/malformed collections and invalid counts do not become
successful empty results. Validation is enabled for these synchronization reads;
other adapter consumers retain their existing response behavior.

This synchronous metadata path does not report pending work. Existing S3 export
job acceptance is separate: a successfully dispatched job can remain `pending`,
with no completion timestamp or exported records. Acceptance alone does not
prove export completion. F04 does not certify the background export pipeline.

Historical logs and their original statuses/details are retained unchanged;
they are not retrospectively certified by the new evidence markers.

## Permissions, retries and recovery

Existing authentication, administrator checks and route action guards remain.
For the synchronization ViewSet, the existing `module_required='data_mining'`
mapping and the route's `sync` action classification mean trigger execution
requires effective `data_mining.update` plus administrator access. Config and
S3 ViewSets use the existing Wrench module mapping. This is inspected permission
behavior, not a new grant or approval of the broader access policy.

Metadata reads allow at most three adapter attempts, with 0.25 and 0.5 second
backoffs, for timeouts, connection failures, or HTTP 429/502/503/504. Permanent
authorization failures (401/403), SSL failures, malformed responses and other
permanent failures stop immediately. The limit is adapter attempts, not an
absolute HTTP-request count: existing bounded 404 endpoint discovery and OData
fallback may issue several requests within an attempt. OData authorization
failures stop discovery instead of probing additional endpoints.

The operation does not automatically replay an external mutation. One log
records the internal read attempts. After exhaustion or a permanent failure,
recovery requires explicit user action; authorization failures require the
integration owner to review access first. No credentials or permissions are
changed by this correction, and no background retry is scheduled by `run_sync`.

## Local verification

From `backend/`, using the existing virtual environment:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
$env:DATABASE_URL = 'sqlite:///:memory:'
$env:AIFLOW_ENVIRONMENT = 'testing'
$env:ENVIRONMENT = 'testing'
$env:USE_S3 = 'false'
..\.venv\Scripts\python.exe manage.py test apps.wrench_integration.tests --settings=config.settings_wrench_test --noinput
```

The isolated suite uses the actual router and module/action guard. Synthetic
HTTP adapters cover supported document/transmittal/OData retrieval, explicit
zero, partial pages, unsupported operations, deceptive success-shaped payloads,
errors, authorization denial, retry limits, and preserved configuration/history.
A mocked Celery dispatch verifies existing S3 pending acceptance. All real
`requests.Session.request` calls are blocked and asserted absent.

Final local result: **22 tests passed** in 4.880 seconds with process exit 0,
including rejection of the internal alias at the public API boundary. Python
compilation and scoped Git whitespace checks also passed.

These SQLite model-sync checks do not validate migrations, PostgreSQL behavior,
live Wrench credentials/contracts, production grants, actual Celery execution or
S3 delivery. No production records, live integrations or infrastructure are used.
