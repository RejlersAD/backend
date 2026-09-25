# System Health: Notification Logs History

Implemented 25 September 2026 as a read-only projection over existing `NotificationLog` events. It does not change notification delivery, personal inbox access, approval authority, retention or schema.

## Endpoint and access

`GET /api/v1/rbac/analytics/notification-history/`

Access matches the existing System Health analytics API: authenticated `IsSuperAdmin` plus the central `admin_dashboard.read` action guard. Current account/profile/module restrictions and explicit read denies apply, including to Django superusers and the `super_admin` role. A normal account with an administration read grant, or an `is_staff` flag, is insufficient. No new permission grant is created.

Only GET, HEAD and OPTIONS are supported. There is no detail, export, resend, mark-read, archive or delete command. The UI's details panel uses the same safe list row and performs no additional source request. Existing `/api/v1/notifications/` and `/api/v1/notifications/logs/` remain recipient-only, including for administrators.

## Query contract

| Parameter | Accepted values |
| --- | --- |
| `hours` | `24` (default), `168`, `720`; uses the event timestamp |
| `page` | Positive integer, default 1; out-of-range pages return 404 |
| `page_size` | Integer 1–100, default 25 |
| `search` | At most 200 characters; recipient full name, username, email or numeric notification ID |
| `channel` | Omitted/empty, `notification`, `email`, `teams`, `web_push`, `other` |
| `outcome` | Omitted/empty, `recorded`, `sent`, `read`, `archived`, `failed`, `skipped`, `unknown` |

Malformed supported filters return 400 with field validation errors. Page numbers and sizes require integer digit syntax. Large or Unicode numeric search text cannot overflow the database's numeric-ID comparison. Rows are ordered by timestamp descending, then ID descending for ties. Search is limited to recipient identity and notification ID; it does not query business content or raw error payloads.

The response uses `count`, `next`, `previous`, `results`, plus `timezone` (the server's current timezone name). `count` counts retained event rows matching the filters, not distinct notifications or recipients. Event timestamps include an ISO 8601 timezone offset.

Each result has only these fields:

```text
id, timestamp, notification_id,
recipient: {id, name, username, email},
category: known uppercase category code or null,
action, event_label, channel, outcome, outcome_label, reason_label,
delivery_status, delivery_status_label, read_status, read_status_label
```

Recipient identity, category and read state are current values joined from the notification/account, not immutable snapshots of those values at event time. Notification title, message, raw lifecycle status, email error, action URL, metadata, raw details, provider exceptions and subscription credentials/endpoints are not returned. Unknown category values become null. Unknown actions become `other` / `Other event`, with channel `other` and outcome `unknown`; their raw text is never returned.

The table replaces its Event column with separate Delivery Status and Read Status columns. Existing event/outcome fields remain available for compatible clients and event details. The status fields are additive; query filters and pagination are unchanged.

| Field | Meaning |
| --- | --- |
| `delivery_status` / `delivery_status_label` | The recorded transport attempt's outcome: `sent`, `failed` or `skipped`, using the precise existing label. Lifecycle events (`created`, `read`, `archived`) use `not_applicable` / Not applicable; unknown events use `unknown` / Unknown. |
| `read_status` / `read_status_label` | Current canonical `Notification.is_read`: `read` / Marked read, or `unread` / Unread. Applies to the parent notification's in-app state, independently of the event's channel/time. |

Archived notifications preserve the canonical read flag. The existing administration mark-unread action can leave the lifecycle status as READ, so neither that status nor a read timestamp overrides `is_read`. Read Status is not an email-open, Teams-read or browser-attention receipt and is not a snapshot at the historical event's time. Loading this endpoint never changes the flag.

## Event meaning

| Recorded action, case-insensitive | Channel | Outcome | Display meaning |
| --- | --- | --- | --- |
| `created` | notification | recorded | Notification created |
| `READ` | notification | read | Marked read; may result from existing automatic read-on-detail behavior |
| `ARCHIVED` | notification | archived | Notification archived |
| `email_sent` | email | sent | Email send recorded |
| `email_skipped` | email | skipped | Email send skipped |
| `teams_sent` | teams | sent | Teams request accepted |
| `teams_failed` | teams | failed | Teams request failed |
| `teams_skipped` | teams | skipped | Teams request skipped |
| `web_push_sent` | web_push | sent | Push request accepted |
| `web_push_failed` | web_push | failed | Push request failed |
| `web_push_skipped` | web_push | skipped | Push request skipped |

These are event outcomes, independent of the notification's mutable current status. A push fanout may have successful, failed and skipped attempts for the same notification; each recorded event remains a separate row. No channel-success event is presented as confirmed recipient delivery. The email service currently logs `email_sent` after calling `email.send()` without testing its numeric return value, so the label deliberately states only what was recorded. Teams success describes the HTTP relay request, not downstream chat delivery; push success does not confirm browser display or user attention.

Only recognized skip reason codes are converted to fixed labels. Unknown, nested or malformed reason/details values yield no reason label. Raw errors are excluded even when they contain a useful troubleshooting message, because they may also contain webhook URLs, tokens or sensitive payloads. No HTTP status or arbitrary provider text is exposed.

## Coverage and retention limits

- This view shows retained `NotificationLog` rows. Direct notification creation and some state changes do not necessarily create a log row.
- Email failures currently update the parent notification's email tracking fields without creating a `NotificationLog` failure event. The view does not manufacture missing attempts from that mutable state.
- Queue failures, disabled/missing providers, tasks that never execute and some early exits may have no persisted event. An absent event is not proof that delivery succeeded or failed.
- Personal notification dismissal deletes its logs through the existing cascade. Deleting a recipient also cascades notifications and their logs. The tab cannot recover removed rows.
- This is not an immutable archive, durable outbox or a complete delivery ledger. No retention period, data backfill, deletion change or capture expansion is introduced.

## Verification

Guarded backend checks passed 54 cases across the history suite, existing personal inbox actions and central action-enforcement regressions after the Delivery Status / Read Status follow-up:

```powershell
..\.venv\Scripts\python.exe manage.py test apps.notifications.tests_admin_history apps.notifications.tests_inbox_actions apps.rbac.tests.tests_action_enforcement --settings=config.settings_permissions_test --noinput --verbosity=2
```

Coverage includes authorized role/superuser access, explicit denial, inactive profile/disabled module, metadata redaction, safe malformed/unknown events and reasons, independent mixed outcomes, period/search/channel/outcome filtering, bounded deterministic pagination, invalid/large/Unicode queries, unchanged recipient-only APIs, forbidden mutations and no read-side effects or external HTTP. Status follow-up checks cover all known transport/lifecycle events, unknown events, archived read/unread flags, inconsistent lifecycle status/read timestamps and changing the current read flag without rewriting past delivery outcomes.

No model or migration file changed. Full-registry `makemigrations --check --dry-run` passed using an isolated in-memory database and notification/cache isolation. The repository's older reduced `settings_migration_check` harness cannot resolve the current Project Control→Procurement migration dependency; the successful check used the actual full registry instead. No application/production migration was run for this feature, and this drift check does not certify a deployed database's applied state.

Evidence: workspace `.codex-temp/notification-history-backend-tests.log` and `.codex-temp/notification-history-20260925/backend-full-registry-drift.log`. The full-registry isolation settings are retained alongside the latter log. Source reads and verification performed no live notification creation, reading, dismissal, delivery or grant change.

Follow-up verification evidence: `.codex-temp/notification-history-status-backend-tests.log` (54 passed). This follow-up changes only the projection, tests and this contract; it introduces no model or migration edit and does not require a migration.

## Main release verification — 25 September 2026

The release candidate combines this history/status feature with the existing compact procurement Teams message changes. It was captured from local development without changing the original working files, then merged with current `origin/main` (`7d779c27`) without conflicts. All 13 captured paths matched the candidate after line-ending normalization before this evidence-only addition.

The aligned candidate passed **183 backend tests** with synthetic records and isolated external delivery:

- 54 history, personal inbox and central action-authorization checks using `config.settings_permissions_test` (command above).
- 87 Teams formatting/context and procurement routing/sequence checks using `config.settings_release_test`: `apps.notifications.tests_teams_formatting`, `apps.procurement.tests.test_notification_context`, `apps.procurement.tests.test_pr_po_notification_isolation`, `apps.procurement.tests.test_po_creation_notification_routing`, `apps.procurement.tests.test_po_notification_sequence`, `apps.procurement.tests.test_requisition_notification_sequence`, `apps.notifications.tests_teams`, and `apps.notifications.tests_business_delivery`.
- 42 adjacent browser-push, approval dispatch and PR creation checks using `config.settings_release_test`: `apps.notifications.tests_push_delivery`, `apps.procurement.tests.test_approval_notification_dispatch`, and `apps.procurement.tests.test_pr_creation_notifications`.

Each group used `manage.py test <modules> --settings=<settings> --noinput --verbosity=2` with the isolated environment from workspace `CONTRIBUTING.md`. These SQLite functional checks do not certify PostgreSQL row locking. The existing pull-request PostgreSQL concurrency workflow remains a separate release gate.

Full-registry `makemigrations --check --dry-run` passed with no changes detected, using the isolated settings described above. A read-only query of the local PostgreSQL migration ledger, compared with the candidate's complete migration graph, found all **528 required migrations applied** and **zero pending**. The local ledger also contains 29 older entries outside that current graph; no history was edited. No model or migration file changes belong to this release. Production migration state was not queried or changed.

Evidence is retained outside the repository under workspace `.codex-temp/merge-notifications-20260925/`: `backend-history-tests.log`, `backend-teams-tests.log`, `backend-delivery-tests.log`, `backend-migration-drift.log`, `backend-migration-state.json`, and `backend-capture/manifest.json`. Git whitespace checks passed. Hosted Teams-flow changes and live delivery are outside this verification; the integration guide explains the existing external flow requirement. This entry records candidate verification, not a completed GitHub merge or production deployment.
