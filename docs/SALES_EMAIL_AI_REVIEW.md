# AI email review and reviewed opportunity creation

The existing Email Intake detail and Create opportunity paths can use bounded,
source-validated AI proposals. The original detection integration added no UI
controls or automatic business decisions. The approved workspace follow-up below
adds Ask RADAI and Analyze unread controls. The rule engine remains available when AI is disabled,
unconfigured, unavailable or produces invalid output.

## Read-only Ask RADAI email review, 29 September 2026

The user explicitly approved functional email questions and unread analysis in
the refreshed Email Intake workspace. Email questions use the existing provider
configuration, source permissions and guarded routes:

- `POST /api/v1/sales/mailbox-connections/{id}/review-assistant/` accepts
  `{message_id, action, question?}` and reloads that message/conversation through
  the server's scoped Graph GET path.
- `POST /api/v1/sales/email-intakes/{id}/review-assistant/` accepts
  `{action, question?}` and uses only the currently accessible saved source and
  same-mailbox conversation history.
- `action` is `question` (default), `extract_requirements`, `check_deadline` or
  `draft_reply`. A question is required for `question`, maximum 2000 characters.
  Chips may omit it. Additional fields, arbitrary context/history and query
  parameters are rejected. Both POSTs require intake **read**, not create,
  permission, plus existing mailbox/intake owner/administrator source scope.

Success returns `{version:1, kind:'answer'|'reply_draft', answer, citations,
provider, model, coverage, partial, needs_review:true}`. Citations contain
`{source_id, excerpt}`. The provider selects relevant exact source passages;
the server validates them and restores their owning and immediately adjacent
paragraphs to retain request labels, negation, corrections and qualifications.
Ambiguous occurrences or oversized evidence
are rejected. At most eight citations, 1800 characters each and 6000 combined,
are accepted. Factual answer content consists of these verified excerpts, not
unrestricted generated claims. No relevant evidence produces an honest
not-established answer. Citation relevance and source truth still require review.

Reply drafts contain a neutral greeting, the source request and explicit editable
placeholders for the user's response, dates and commitments. `kind` remains
`reply_draft` when no evidence is available. They are text proposals only: neither
endpoint sends mail, saves a mailbox draft, marks messages read, changes review
or classification state, nor creates a client/opportunity/audit record. Agreement
return and proposal submission facts retain their source labels; no answer
changes the proposal form or canonical business status. Attachments/portal links
are not followed and no tools are available to the model.

Disabled/missing configuration and provider failures return a real `503` with
`code:email_assistant_unavailable`; timeout returns `504 email_assistant_timeout`;
invalid source citations return `502 email_assistant_invalid_response`. Existing
input/source errors remain 400/403/404. Error details contain no provider payload
or credential. Responses use private/no-store HTTP headers. Five-minute result
caching is keyed by actor, source content/identity, question/action, provider
configuration and contract, with a bounded concurrent-call lease. Every request
rechecks source access before cache reuse; lists/conversion do not invoke this
assistant. This adds no model migration, dependency or provider activation.

Verification: 146 focused/retained cases passed in 38.204 seconds in a fresh
network-disabled Python 3.11 container. The 25 new assistant cases exercise
read-only grants, denied/foreign/anonymous sources, saved-history scope, malformed
requests, disabled/error/timeout behavior, invented citations, restored negation,
adjacent corrections/owning request labels and oversized-context refusal,
prompt injection boundaries, scoped caching and unchanged business rows. Retained
provider, browsing, saved-analysis, classification and conversion cases also pass.
These regression tests mock provider and Graph calls. They make no live mailbox
read or production deployment claim. Evidence: `artifacts/email-review-assistant-final.log`.

Separate real-provider smoke check, 29 September 2026: one synthetic OQ tender
question using `check_deadline` completed through the existing locally configured
Anthropic adapter and the current assistant citation validator. Supported date
(2 October 2026), time (01:59), timezone (Gulf Standard Time), exact source
matching and mandatory review checks all passed. This used fresh in-process
LocMem cache and minimal Django settings, with no application database setup,
mailbox access, business writes or credential changes. It verifies this bounded
synthetic case, not production configuration or general model accuracy. Only
provider/status and Boolean checks were recorded in
`artifacts/email-review-assistant-anthropic-smoke.log`.

## Release preparation, 29 September 2026

The scoped release was assembled on fetched `main` at
`dcffd07bae3a587b64d0c2364e0e33005c7c5987`, preserving its newer shared-mailbox
setup, reviewed classification, HMB and RBAC changes. The agreement regression
suite is explicitly included despite the repository's general test-file ignore.

Against that release source, 447 functional and migration regressions passed in
93.010 seconds in a fresh, network-disabled Python 3.11.16 container with
Anthropic SDK 1.0. Tests include source validation, provider transport,
live/saved review, canonical client resolution, stale/retry/denial/rollback,
agreement semantics and retained mailbox setup. Historical migration fixtures
apply/reverse existing Sales 0008, 0009 and 0010 operations on private SQLite.
A separate full-registry comparison loaded 58 apps and 533 migration files,
finding no conflicts or model drift. No application database was used and this
feature adds no migration. Logs are `artifacts/release-email-ai-functional.log`
and `artifacts/release-email-ai-schema.log` in the release worktree.

These checks are release preparation, not proof of production activation.
Deploy the backend before the frontend, then explicitly configure and verify
the chosen provider on the intended Railway backend service. The repository's
Railway pre-deploy command runs the complete migration graph; an existing live
schema mismatch remains an operational deployment condition. Backend settings
default email AI off, and local environment files do not enter the image.

## Configuration

The release branch also passed all eight email concurrency cases on disposable
PostgreSQL 15 with Python 3.11. The database had no published ports or application
network access; tests observed real lock waits for client/source/tender retries.
Django removed the test database, and the identified temporary container was
removed. Evidence: `artifacts/release-email-ai-postgresql.log`. This complements
the 447 release functional/migration cases and full-registry state comparison.

### Agreement correspondence facts

The agreement-reply follow-up adds source-backed `agreement_reference`,
`correspondence_reference` and `action_deadlines` to extracted information and
reviewed snapshots. A Q-prefixed reference is literal correspondence context,
not a resolved Quote record. Agreement signing/return deadlines have kind
`agreement_return`, date, optional clock/literal timezone, source IDs, exact
evidence and `requires_verification` status. They never become Proposal deadline.

Bounded source rules retain these facts even without AI. AI validation also
checks reference and deadline meaning, so a literal WO number or agreement date
cannot qualify as a tender identifier or bid-submission date merely because its
text appears in the source. Genuine separately evidenced tender fields remain
supported. The latest response and quoted actions remain distinct; no completion,
award, commercial value or canonical opportunity link is inferred. Existing
review and explicit save controls remain unchanged. Verification status is in
the workspace [brief](../../docs/features/sales-email-agreement-actions.md).

### Provider settings

Enable only on the backend that is authorized to process the mailbox content:

The user selected Anthropic/Claude for activation. Configure:

```dotenv
SALES_EMAIL_AI_ENABLED=true
SALES_EMAIL_AI_PROVIDER=anthropic
SALES_EMAIL_AI_MODEL=claude-sonnet-5-5
SALES_EMAIL_AI_TIMEOUT_SECONDS=30
```

Save the Anthropic secret privately as `SALES_EMAIL_AI_API_KEY`. Alternatively,
Anthropic reads server `ANTHROPIC_API_KEY` and `ANTHROPIC_MODEL` when the email
overrides are blank. The model must be explicit; no OpenAI key or model is ever
used for an Anthropic request. An existing email override takes precedence, so
replace an old OpenAI email override when switching providers. The adapter never
silently switches providers on failure.

Existing OpenAI setups remain supported with `SALES_EMAIL_AI_PROVIDER=openai`;
their blank email-specific overrides use `OPENAI_API_KEY` and `OPENAI_MODEL`.
For backward compatibility the provider setting still defaults to `openai` and
the enable flag defaults off. Configure keys through the server secret mechanism,
not source control or a browser form. No project BYOK credential is borrowed.
A key's presence is configuration readiness, not proof of authentication.

The model example and JSON format follow the official
[Claude model overview](https://platform.claude.com/docs/en/models/overview) and
[structured-output contract](https://platform.claude.com/docs/en/build-with-claude/structured-outputs),
checked on 29 September 2026. Model access and live latency still require a real
credential check. Structured schema compilation can add first-request latency;
the example uses the existing maximum 30-second timeout.

`SALES_EMAIL_AI_TIMEOUT_SECONDS` defaults to 12 (allowed 1–30).
`SALES_EMAIL_AI_MAX_OUTPUT_TOKENS` defaults to 3500 (allowed 256–6000).
The installed provider SDKs send schema-constrained requests to their official
endpoints: OpenAI Chat Completions with `store=false`, or Anthropic Messages with
`output_config.format`. No tools or automatic retries are used. This does not
assert universal zero-retention or company-wide data-policy approval. Provider
errors are returned as static categories; raw errors, source text and secrets
are not logged by this transport. Only validated text proposals can enter the
email review contract; any provider reasoning blocks are excluded.

### Local and Railway activation locations

The user requested both environments, with one-step-at-a-time guidance. The
workspace's local Docker Compose backend uses root `.env.local` via `env_file`
and mounts `backend/` at `/app`. Injected environment variables take precedence
over the mounted backend `.env`. Edit the root file privately, then recreate
`backend_local` so new environment values are loaded; a plain container restart
does not replace injected values. Do not print the environment or secret file.

For Railway, set these variables on the backend service in the intended Railway
environment. Local `.env` files are excluded from the backend image. The updated
integration code must be deployed and the service restarted/redeployed with its
new variables before activation. Local success is not production activation;
release/merge authority remains governed by workspace instructions.

## Execution and response contract

- Current source/read/connection scope is checked before detail analysis.
  Lists do not trigger one provider call per inbox row. Saved selection requests
  detail using the existing GET route; live detail uses the authorized Graph read.
- The existing conversation parser assigns source IDs and direction/quote roles.
  Only retained non-draft, non-outgoing segments are sent; source count/text is
  bounded and partial coverage remains explicit. Attachments and portal URLs
  are not fetched. Source instructions cannot grant tool or business authority.
- The private schema returns a selected-source category/purpose, field claims
  and conflicts. Claims require a supplied source ID and an exact excerpt, with
  whitespace normalization only. Literal identity/reference fields must occur
  in their excerpt; dates, decimal values, timezone precision and conflicting
  evidence receive additional validation. These checks do not certify perfect
  semantic understanding or commercial suitability.
- Existing `detection_version: 2`, classification version 1/enums and intelligence
  version 1 remain. A tender reminder is `tender_opportunity` with separate
  `ai_review.purpose: reminder` and a `follow_up` opportunity suggestion.
  The original incoming sent date stays separate from the submission deadline.
- AI confidence uses `method: ai_evidence_v1`, categorical evidence strength and
  human-review wording, never a claimed probability. The existing components
  also continue accepting `rule_evidence_v1`.
- `ai_review` includes version, status, provider/model, purpose, analysis identity,
  accepted proposal/field evidence, rejected/conflicting fields and coverage.
  Status `validated` means the application checks passed, not that a person
  approved the interpretation. Failure statuses preserve rules-only suggestions
  and explain the limitation through existing analysis disclosures.
- Exact time/timezone is retained in analysis and signed review evidence. An
  aware `deadline_at` is emitted only when date/time/explicit offset evidence
  supports it; ambiguous abbreviations do not receive guessed offsets. The
  existing Deal `submission_due_date` and form remain date-only. No automatic
  deadline reminders or operational timestamp scheduling is introduced.
- Numeric references cannot borrow a nearby budget label; unsupported ranges,
  percentages and scaled amounts remain unresolved. Date, clock and timezone
  must belong to the same deadline block. Document update dates and briefing
  times cannot complete a submission deadline, and timezone prefixes cannot
  change an explicit UTC/GMT offset.

## Cache and reviewed creation

Validated analysis is cached for one hour under a hash of actor/source scope,
bounded source/baseline, contract version and provider configuration identity.
Changed content, model, credentials or instructions invalidate reuse. Cache
availability is checked before spending provider calls; concurrent analysis
uses a bounded lease. Provider/validation failures have short cache backoff.
The cache is not a durable analysis archive. Read-time processing never changes
source snapshots, canonical clients, opportunities or review records.

The existing live signed source token additionally binds a compact reviewed
analysis snapshot. Saved detail returns an equivalent hidden `source_token` tied
to actor, intake and available source history. New frontend saved conversions
send it without adding controls. Legacy manual saved callers may omit it, but
then gain no claim of reviewed AI provenance. Source change/expiry requires a
reload; conversion revalidates raw evidence without another provider call.

Creation still requires explicit classification, reviewed client resolution, valid
reviewed commercial inputs and existing permissions. Missing estimated value or
award date is not invented. The current form preserves inputs on failed submits.
The exact reviewed analysis is retained in the atomic opportunity audit as
`reviewed_email_analysis`; the Deal records the user's final form values.

Both conversion paths lock the canonical client and reject an exact,
source-evidenced tender reference already present for that client, accounting
for known differing portals using retained creation-audit evidence. Editable
opportunity custom fields cannot remove this source identity. This is a review
conflict (`409`, code `email_tender_already_exists`) through existing form
errors, not authority to link, merge or update an existing opportunity. Source
retry protection remains separate. Uncertain lots/reissues require human review.

### Form autofill and missing clients

The existing form prefills supported email values. A unique exact canonical
match is preselected; a supported unmatched explicit organization can preselect
the existing new-client option when `can_create_client` is true. Users may
override the choice. Ambiguous/denied/unsupported claims stay unresolved, and
opening the form makes no client or opportunity write.

Both conversion commands accept `client: <UUID>` or
`new_client: {"company_name": "<reviewed detected organization>"}`. Signed review
requests reject additional new-client metadata or a name inconsistent with the
reviewed source. Legacy saved manual creation retains its explicitly supplied
metadata contract. The server uses the current actor's client visibility and
NFC/casefold/whitespace-normalized company, legal and trading names; it does not
guess acronyms, suffixes or domain identity. One accessible match is reused,
ambiguity/inaccessible matches receive `409 email_customer_conflict`, and actual
creation additionally requires client-create authority.

New signed-review clients retain only the company name, generated code, existing
Other industry category, Prospect/Unverified defaults and acting account manager.
Legal names, contact details and verification are not invented. Client creation,
Deal creation and `reviewed_customer_resolution` audit commit atomically; failed
validation/audit must leave no orphan client. The canonical Sales Client list
therefore displays the same record linked by the opportunity.

Name resolution uses a short PostgreSQL `SHARE ROW EXCLUSIVE` lock on the client
table after source/network work, followed by the existing canonical row lock.
This serializes email name-resolution/create decisions and waits for in-flight
generic client writers; it is not a global name-uniqueness rule for later generic
API writes. Existing explicit-ID conversion retains its row lock.

The form accepts source-stated three-letter currencies outside its usual list,
such as OMR. Decimal-equivalent amounts and literal same-source scope extensions
are not mistaken for conflicts. Explicit standard scope names map to the existing
enum, including Other only when expressly labelled. Validator revision is part
of cache identity. Missing facts still require review/manual entry, and distinct
or superseding evidence remains unresolved. Public analysis versions are unchanged.

## Verification boundary

The autofill/client-resolution follow-up passed 62 backend functional cases,
50 focused AI validator cases and eight disposable PostgreSQL concurrency cases
across targeted runs. Full-registry comparison found no model drift or migration
conflicts (58 apps, 533 migration files), without using the application database
or applying migrations. The frontend passed 77 distinct browser cases, scoped
lint and the Node 20 production/PWA build. Local web code was restarted and is
healthy; no real client/opportunity save or Railway rollout was performed.
Additional coverage is in `test_email_client_resolution.py` and
`test_email_client_concurrency.py`; logs are under `artifacts/email-client-*.log`
and `frontend/artifacts/email-ai-review/prefill-*.log` in the separate frontend repo.

See `apps/sales/tests/test_email_ai_analysis.py`, `test_email_ai_provider.py`,
`test_email_ai_review_integration.py` and `test_email_tender_concurrency.py`
for synthetic source validation,
configuration/refusal/failure, scoped reuse, live/saved detail and opportunity
guard coverage. Local checks passed 183 engine cases, 115 provider/retained API
cases, 11 new API integration cases and a real PostgreSQL concurrent-tender case
across focused runs. Full-registry model/migration checks found no drift or
conflicts (58 apps, 533 migrations); this did not apply migrations. The frontend
passed 134 distinct browser cases, scoped lint and its Node 20 production build.
Mocked provider tests prove application behavior; they do not
establish model quality or live credentials. An actual call with the user-supplied
OQ example on 29 September 2026 using the locally configured backend key returned
`provider_authentication`. The user subsequently chose Anthropic; that earlier
OpenAI failure says nothing about their Anthropic key. After the user entered
local Anthropic settings and recreated the backend, one real supplied-example
request completed with source-validated results in 9.33 seconds. The local
backend was healthy. OQ, all three references, portal and full deadline matched;
the accepted scope summary was source-backed but not identical to the fixture's
expected wording. Missing value/currency/award date stayed blank. This check used
no live mailbox read or business database and created no records. Local screen
acceptance and Railway activation remain pending. No schema migration or
production deployment is claimed.

Anthropic follow-up: all 29 provider cases passed on installed host SDK 0.121
and Docker SDK 1.0, including the retained 15 OpenAI cases. The OQ fixture using
the real Anthropic SDK with mocked HTTP responses passed extraction/validation,
scoped reuse and authentication-error fallback. Alongside 49 retained analyzer
and creation API cases, 79 cases passed across focused follow-up runs. This
checks application behavior and SDK serialization, not live model accuracy.
