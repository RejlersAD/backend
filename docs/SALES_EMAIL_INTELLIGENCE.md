# Sales email intelligence version 2

Local implementation, 28 September 2026. This document describes source contracts
and operation; it does not certify production deployment. The workspace authority
and final verification record are in
[the feature brief](../../docs/features/sales-email-intelligence-reference.md).

## Approved meanings

Existing saved/live analysis returns `extracted_information.detection_version: 2`.
Consumers must use the version when interpreting fields whose meanings changed.
Keep five visible field labels: Title (Subject), Customer Name, Submission Date,
Due Date and Type of Request. This final contract supersedes earlier domain-only
Customer Name and label-hiding wording; supporting reasons remain available in
the evidence disclosure.

| Field | Version 2 meaning |
| --- | --- |
| `title` | Evidenced original request subject, with original/coverage qualifications retained |
| `customer_name` | Explicit source-evidenced organization when available; otherwise a qualified low-confidence readable label derived from the evidenced external customer domain |
| `customer_domain` | Separate supporting customer-domain evidence, not canonical organization identity |
| `organization_name`, `company_name` | Separately evidenced organization name; canonical matching uses this evidence |
| `submission_date` | Original incoming email's genuine sent calendar date, or blank if unconfirmed |
| `submission_date_text` | Supporting original sent timestamp/header text |
| `stated_submission_date`, `stated_submission_date_text` | Retained body-stated submission information from the earlier contract |
| `due_date`, `deadline_date` | Current proposal deadline supported by source text, separate from sent time |
| `request_type_code` | Type anchored to the original solicitation; later incidental words cannot override it |

Request/field extraction remains deterministic and bounded. Multiline invitations,
budgetary quotations and natural deadline clauses reuse the shared extractor;
conflicting, hypothetical or ambiguous evidence remains reviewable. Known shared
procurement/public/internal sender domains cannot establish the customer domain.
SAP Ariba requires separately evidenced customer-domain information, otherwise
human verification. Recognized domain lists are technical safeguards, not a
complete identity registry. A readable domain-derived display name is only a
low-confidence proposal and never populates canonical organization identity,
canonical matching or client creation. Explicit organization evidence takes
precedence over that fallback.

The original solicitation anchors request type independently of selected-message
classification. Later incidental request words, administrative instructions and
quoted references cannot override it or manufacture a conflict. Only an assertive
positive later reclassification of the solicitation creates a review conflict;
negated, hypothetical and referenced alternatives do not.

An original source must be evidenced; the first available reply is not proof that
the original was retrieved. A genuine aware sent timestamp can establish its date.
An unambiguous quoted Sent/Date header can establish a calendar day without proving
an absolute timezone-aware instant. Received timestamps, body deadlines and latest
reply dates cannot substitute. Existing chronology metadata may use reception time
to order sources; that does not make it sent evidence.

For a non-request notice, missing reply references alone are insufficient. The
transport-start fallback requires complete supplied coverage, one header-established
actual-message start, aware genuine sent timestamps for all incoming sources, and
that candidate uniquely earliest. Partial, saved-only and selected-only history
cannot confirm this fallback; original request evidence remains a separate basis.

## Review metadata and authority

`intelligence.version: 1` contains:

- `source`: original source ID, basis and confirmation state.
- `customer_name`, `customer_domain` and `submission_date`: status, reason, source IDs and date
  basis where available. Status distinguishes detected, requires_verification,
  not_detected and conflicting rather than fabricating missing values.
  Customer-name basis distinguishes `explicit_organization` from `domain_label`;
  the latter retains low confidence and human verification.
- `entities`: bounded organization/contact/email/domain/project proposals with
  evidence excerpts and source IDs.
- `deadline_review`: detected/not_detected/requires_verification/ambiguous, reason
  and source IDs. Portal documents and attachments are not opened; verify their
  deadlines manually even when email text supplies a date.
- `opportunity_detection`: candidate/follow_up/not_established/ambiguous, reason,
  source IDs and `needs_review: true`.
- `field_confidence`: high/medium/low/unresolved, `method: rule_evidence_v1`,
  reasons and source IDs. The top-level `confidence` map mirrors this data.

Analysis also exposes nullable `original_incoming_source_id` and per-source
`is_original_incoming`, separately from selected, first-available incoming and
original-request markers. A field's evidence/source references remain inspectable.

These confidence levels describe rule/source quality, not calibrated probability,
win likelihood, company eligibility or approval. `opportunity_detected` is a derived
suggestion only. No client, opportunity, audit or source record is created on read.
The existing nine-field form remains explicit; proposal deadline and expected award
date never use Submission Date as a substitute. Preserve user edits across failures
and source refresh, and recheck source freshness and authority on conversion.

`customer_match` remains version 1 / `exact_name_v1`. For detection v2 it consumes
`organization_name` and its evidence/source references, then compares authorized
canonical company/legal/trading names. Neither a raw display domain nor its derived
human-readable label can supply this match or create a client. Current
`sales_clients.read`, row visibility, request-local isolation,
candidate ambiguity and explicit client choice still apply. Missing/denied matches
cannot disclose hidden identities or justify automatic client creation.

## Sent-time storage and rollout

`SalesEmailIntake.sent_at` is nullable and readonly in the API/admin. Migration
`0010_email_sent_at` adds the field without a default or historical data update.
New manual/sync captures preserve a provider's validated aware timestamp. Missing
or null sent time remains null. Explicit malformed, naive or non-string provider
values fail safely before saving; no implicit timezone or received-time fallback.

Capture `get_or_create` retries never alter the retained source or review state.
Sync items already marked captured are not refetched for enrichment. Consequently,
older rows can remain null indefinitely; applying the migration or replaying delta
pages is not a sent-time backfill. Any future enrichment needs its own scoped,
reviewed operation. Analysis may corroborate an original's missing date from an
equivalent quoted source without rewriting the stored record.

Coordinate local rollout against the explicitly verified development database:
briefly stop Beat/worker scheduling, retain durable SQL state, apply migration 0010,
then reload API, worker and Beat so new model/capture code agrees with the schema.
Local compose names are `radai_backend_local`, `radai_celery_local` and
`radai_celery_beat_local`. Preserve enabled/paused state, authority, pending items,
checkpoints and leases; do not reset ingestion to populate dates. Verify prior
columns/business records unchanged and historical sent times null. Compare old
source hashes over their original columns, since the additive null field changes
the shape of an unrestricted `values()` snapshot.

Reverse migration succeeds when every sent value is null. If sent evidence exists,
the reverse guard refuses before dropping the column. Keep the compatible schema
or restore a verified pre-migration backup; do not erase evidence to force rollback.
Live reviewed-source hashing already includes sent time; adding this saved field
does not rewrite historic opportunity audit hashes or relax retry checks.

## Runnable shared-engine reference

[examples/sales_email_intelligence_reference.py](examples/sales_email_intelligence_reference.py)
adapts normalized JSON messages into the production conversation engine. It replaces
the attachment's illustrative hardcoded clients, constant confidence and keyword
score with actual shared logic. It is an offline adapter, not a mailbox connector.

From `backend/`, with the repository Python environment:

```powershell
..\.venv\Scripts\python.exe docs/examples/sales_email_intelligence_reference.py input.json
```

Example synthetic UTF-8 JSON envelope:

```json
{
  "selected_message_id": "synthetic-original",
  "mailbox_address": "sales@internal.test",
  "coverage": {"status": "selected_only", "reason": "One supplied synthetic source."},
  "messages": [{
    "id": "synthetic-original",
    "subject": "Request for quotation: synthetic study",
    "sender_name": "Synthetic buyer",
    "sender_email": "buyer@customer.test",
    "sent_at": "2026-09-28T08:00:00Z",
    "received_at": "2026-09-28T08:01:00Z",
    "body_text": "Customer: Synthetic Utilities Ltd\nPlease submit your quotation.\nDue date: 12 October 2026"
  }]
}
```

Each message can also carry normalized reply-header metadata supplied by the trusted
application transport. Do not manufacture it from absence of a field. Sent metadata
must be genuine sent evidence. CLI input is limited to 8,000,000 bytes; engine
conversation/text/segment limits still apply and coverage remains qualified.

The callable `analyze_email(messages, selected_message_id=..., mailbox_address=...,
coverage=..., customer_matcher=...)` optionally accepts the application's current
authorized `EmailCustomerMatcher` after actor/source authentication. The CLI does
not initialize client access and reports matching unavailable. It performs no
network calls, database writes, attachment reads or automatic opportunity creation.
Output contains supplied source evidence; use synthetic/private input and do not
publish real email output in application logs.

## Verification record and limits

An isolated Python 3.11 run passed **82 capture/sync/admin/projection/migration
tests** in 40.010s. The migration tests execute actual historical `0009` to `0010`
DDL and guarded reversal in private SQLite, preserving source/review/business and
sync data. The full application registry's private SQLite migration-state check
reported no Sales model drift. Logs: `artifacts/sales-email-sent-at-tests-final.log`
and `artifacts/sales-email-sent-at-migration-state-full.log`.

The documented adapter also ran against the synthetic envelope above on Python
3.11: version 2, expected sent date/domain, one evidence source, and customer
matching unavailable without an authorized matcher. No database or network was used.

Whole-feature verification subsequently passed 375 backend tests, then 155
affected tests after the final clarification correction; 47 distinct browser
cases, scoped frontend lint and the Node 20 production build also passed.
Actual local PostgreSQL migration 0010 succeeded with 562 migrations applied,
10 for Sales, no pending migrations and no model drift. Preexisting source,
review, business and sync state stayed unchanged; all 1,175 historical sent
values remained null. The backend, worker and Beat were reloaded. Three saved
and two live authorized read probes succeeded without business or Graph writes.
Full evidence and runtime limits are recorded in the workspace feature brief.
This does not certify production deployment or exhaustive language/domain accuracy.
Missing historical sent metadata, bounded/partial threads, ambiguous wording and
unread linked/attached documents remain explicit limitations.
