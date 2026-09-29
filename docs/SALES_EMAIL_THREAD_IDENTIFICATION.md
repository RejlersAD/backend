# Original email and reply identification

Local correction, implemented and verified 28 September 2026. No production release.

## Derived contract

Existing extracted_information.analysis gains selected_source_id,
original_request_source_id and first_incoming_source_id. Source records add
thread_role (new_message/reply/forward/draft/unknown), direction, role basis and
reason, and is_selected/is_first_incoming/is_original_request flags. These are
derived evidence, not persisted workflow status. Read/unread, incoming/outgoing,
classification and notice purpose remain independent.

An earliest available incoming actual message may be a reply. A quoted fragment
may contain the original request without being a separately retrieved mailbox
message. Missing or uncertain history is explicitly limited. Original-request
fields do not replace the selected message's raw header or preview.

## Saved history

saved_email_analysis requires an authenticated active actor and current intake
read before consulting siblings. It reuses visible_email_intakes and filters the
exact nonblank conversation ID, connection, source tenant/address and current
connection identity. No subject grouping, external fetch or inferred legacy
mailbox identity is permitted. Saved capture includes incoming messages only;
outgoing/uncaptured sources may be missing, so coverage stays saved_content.

History is ordered by received timestamp and stable identity, capped at 100
records/one million characters per conversation and twenty million characters
per response. A request-local cache shares raw history across list rows. The
selected source is always supplied first to preserve its preview/classification
within existing analyzer limits; sources are then ordered by the analyzer.
Limits produce partial coverage, not a complete-thread claim. No captured source,
review state, client, opportunity or audit row is changed by these GETs.

The cache bounds retained history and database reads; each list row still
analyzes its selected message against that history. It is not a total response
CPU or serialized-output limit. Large saved conversations can increase list size.

The intake serializer memoizes same-actor opportunity/client creation display
grants only within its response. Row status remains evaluated individually;
each new response and every conversion command rechecks authority. Customer
matching retains its current read checks. This avoids thousands of repeated
grant queries on the existing 500-row saved register without a shared auth cache.

## Live history and source review

Repeated quoted copies with the same sender, subject, body and dated header are
deduplicated before source limits. Timezone-naive header text is compared as
literal evidence only; it is never converted to an assumed timestamp or used to
merge a quote with an independently retrieved message. Missing/oversized dates
and different senders/dates remain separate. Missing timezone is still reported.

Thread metadata is requested in existing scoped Graph reads. Header values are
untrusted bounded evidence. Do not fetch external header targets or expose raw
provider identifiers in the timeline. Preserve strict continuation/conversation
validation and include material evidence in the review digest. Conversion still
requires explicit reviewed input, fresh source and current independent authority.

Microsoft documents internetMessageHeaders and its explicit selection in the
[message resource](https://learn.microsoft.com/en-us/graph/api/resources/message?view=graph-rest-1.0)
and [get-message API](https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0).
Only bounded In-Reply-To/References values are retained internally. Separate
delegated Sender evidence is bound when it differs from From. Neither internal
header data nor private provider identities are added to timeline source cards.

## Verification

See tests/test_saved_email_analysis.py and the engine/conversation/UI regressions.
327 broader functional cases passed; after the final display-grant optimization,
78 relevant API/saved/conversion cases passed, including the new scaling and
fresh-revocation case. Actual local schema has 561 applied migrations, zero
pending/conflicts and no model drift; no schema changes were needed. Three saved
and two live real-source checks passed without source/review/business changes.
The 500-row saved list measured 19.116s/2,451 queries after the optimization,
versus 59.378s/24,425 before; ongoing sync changed the dataset between reads.
The 6.3 MB response and bounded/partial conversation coverage remain limitations.
See artifacts/sales-thread-*.log and metadata-only JSON reports. The workspace
brief docs/features/sales-email-thread-identification.md records the full
verification, frontend build/browser evidence and local reload boundary.
