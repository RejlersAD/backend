# Sales email automation: durable capture (Task 1)

This extends the existing Sales intake store with an explicit, mailbox-scoped
capture command. Live mailbox GET browsing remains transient and read-only.
It does not enable scheduled import, create leads, or alter Microsoft mail.

## Command and access

`POST /api/v1/sales/mailbox-connections/{connection_id}/capture-message/`

```json
{"message_id": "immutable-id-from-the-authorized-mailbox-list"}
```

Only this field is accepted, as a string of at most 512 characters. No caller
mailbox address, body, sender, actor, workflow status, client or date is accepted.
The actor needs both `sales_email_intake.read` and `.create`, module access, and
the established mailbox owner/administrator scope. `is_staff` alone is not
mailbox administrator access. Application connections are supported.

The backend makes one GET for the actual selected message, with immutable IDs,
body and conversation metadata. It accepts only confirmed incoming, non-draft
mail; unknown direction and outbound mail remain available in the live viewer.
Dates must be valid timestamps with timezone, conversation ID nonempty, and the
source must fit field limits and the existing one-million-character body budget.
Unsupported sources fail safely rather than being silently truncated.

After the read, the server locks the connection, reloads actor authority and
rechecks scope/configuration. It creates the row only once. Responses:

- `201 {"intake": {...}, "created": true}`: first retained source snapshot.
- `200 {"intake": {...}, "created": false}`: retry, retaining all source,
  original capturing actor/time and review state. Retries still require current
  permissions and a successful authoritative Graph read.
- `400`: invalid command or ineligible direction; `401/403`: access denial;
  `404`: unavailable source/connection; `409`: configuration changed;
  `502/503`: safe provider/storage failure. No email content/provider diagnostics
  appear in error responses. Capture and saved-intake responses are private/no-store.

The existing saved review API `/api/v1/sales/email-intakes/` returns additive
read-only `mailbox_connection`, `source_mailbox_address`, `source_tenant_id`,
`conversation_id`, and `captured_by`. Saved plaintext stays in `body_preview`.
The source snapshot is not a complete mailbox archive, original MIME, attachment
store or independently fetched conversation. Separate messages can share the
same mailbox/conversation ID; each retains its own immutable message identity.

Saved rows can be reviewed at `/sales/email-intake?view=imported`. This task adds
no frontend control or implicit write when selecting/refreshing a live email.

## Identity, historical records and administration

Migration `sales.0008_mailbox_scoped_capture` replaces the global message-ID
constraint with `(mailbox_connection, source_message_id)` uniqueness plus
conditional legacy message-ID uniqueness for null-mailbox records. Identifiers
are case-sensitive; Internet Message-ID and conversation ID are not unique keys.

Historical rows keep null mailbox ownership and the established legacy module
visibility. The Power Automate webhook remains explicitly in that legacy bucket;
it rejects attempts to supply mailbox scope or new source metadata. It cannot
match/return an existing captured row merely because message IDs are equal.

Captured rows use current connection owner/administrator scope for list, search,
detail and review commands. Duplicate targets must be accessible and in the same
mailbox bucket. Related duplicate/opportunity IDs and names are redacted when
their records are no longer accessible. The protected connection FK prevents
deletion from orphaning a captured row into legacy visibility.

Captured connection identities cannot be repointed through configuration or
delegated OAuth; configuration and capture serialize on the same connection.
Names, health and intake-enable settings retain their existing separate meaning.
Captured sources are view-only in Django admin, with scoped queries/counts and
no generic source creation, mutation or deletion. Existing legacy review remains.

## Migration and recovery

Roll out schema before capture code is used. Stop old webhook writers during a
mixed-version transition: their global identity lookup does not understand the
new scoped identity. Deploy the updated webhook and capture code together before
enabling any capture caller. No continuous worker is enabled by this migration.

Migration is additive apart from replacing uniqueness constraints. Existing
source and review fields are unchanged, with no guessed mailbox backfill.
Rollback before any captured rows exist can reverse normally. After capture,
the migration refuses reversal before removing constraints/columns, because
that would lose provenance and could violate historical global uniqueness.
Stop writers and keep the compatible schema, or restore a verified pre-capture
backup under an explicit recovery plan. Do not delete evidence to bypass this guard.

## Verification

Functional checks use `config.settings_release_test` for the new capture/admin
tests and existing mailbox, Graph, conversation and intake/opportunity suites.
Concurrency checks use `config.settings_procurement_postgresql_test` and a
separately provisioned disposable PostgreSQL, per the existing concurrency runbook.
The functional model-sync harness does not certify migrations; historical-state
schema operations and actual local full-registry checks are recorded separately.

Verified locally on 28 September 2026: 165 functional/migration tests passed in
Python 3.11, plus three observed-lock PostgreSQL concurrency cases. Actual 0008
DDL tests preserve five historical intake states and guard reversal before any
DDL once captures exist. Actual local PostgreSQL reports 560 applied migrations,
eight for Sales, no pending/conflicting migration or model drift. The local
intake table was empty before migration; historical preservation is exercised by
the isolated synthetic migration test, not inferred from that empty table.

A real incoming shared-mailbox email returned 201 on capture and 200 on retry,
retaining one unchanged source. After local backend restart, saved detail was
still available with the mailbox/conversation link. Three Graph GETs and zero
Graph writes were observed. No opportunity was created and mailbox configuration
was unchanged. This is local validation only; continuous sync and production
deployment remain separate tasks. Logs: `artifacts/sales-capture-*.log` and
`artifacts/sales-mailbox-capture-postgresql.log`.

Microsoft references: [immutable IDs](https://learn.microsoft.com/en-us/graph/outlook-immutable-id)
and [GET message](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0).
Immutable IDs survive moves within a mailbox; moving to an archive mailbox or
export/reimport can create a different identity. No cross-archive deduplication
or production compliance certification is claimed.
