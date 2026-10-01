# Bid and proposal preparation

## Temporary bid-decision access (1 October 2026)

Before proposal preparation, `POST /api/v1/sales/deals/{id}/bid-decision/`
now allows current Opportunity module readers when the exact business route
`sales_opportunities.Deal.bid_decision` is absent. This user-authorized fallback
defaults on via `SALES_BID_DECISION_RBAC_FALLBACK_ENABLED`. It rechecks current
account/read permission, explicit approval-deny overrides and Sales record/
organization scope under the Deal lock. No separate approve grant, business
position or high-risk/high-value owner separation is required in fallback mode.

Configured routes always take precedence; malformed policy fails closed.
Set `SALES_BID_DECISION_RBAC_FALLBACK_ENABLED=False` to restore mandatory formal
routing, then configure the approved route through the existing deployment
policy. Stage/value/currency/reason validation and atomic audit remain. Audit
JSON records `decision_authority` as `module_rbac` or `configured_route`.
Other approval operations, database schema and saved history are unchanged.

Local verification passed 27 Sales workflow/access cases and 36 shared RBAC
regressions, scoped lint and whitespace checks. Logs are
`artifacts/bid-decision-access-tests.log`,
`artifacts/bid-rbac-business-approval-regressions.log`, and
`artifacts/bid-rbac-action-enforcement-regressions.log`. Local backend reload and
effective-mode checks are recorded in `artifacts/bid-decision-local-activation.log`.
No real opportunity decision, migration or production deployment was performed.

Lifecycle order 2 connects canonical Sales Deal/Quote with existing Planning work.
It does not create an awarded core Project, grant source access, set financial
values, approve a technical/commercial proposal, or send external correspondence.

## Ownership and persistence

Sales migration0015 adds BidPreparation (one opportunity and one PlanningProject),
QuotePreparationRevision (immutable reviewed source captures per Quote), and
BidPreparationCommand (unique actor/request UUID). Existing records are not
automatically matched or changed. A reverse guard refuses schema removal after
any connection exists; application rollback retains this additive schema.

Known opportunity/client/name/scope/location/start/duration facts initialize a
new standalone Planning workspace. Missing duration requires an explicit positive
draft planning assumption, at most four decimal places; source_basis records
whether duration came from the opportunity, reviewer or existing workspace.
No unknown dates or duration are invented. Existing attached workspaces retain
their authored facts. Deal client identity is retained after connection.
Planning's title is limited to its existing255-character field; the complete
opportunity name remains in the canonical Deal and connection source_basis.

## API contract

All paths are relative to `/api/v1/sales/`; responses are private/no-store.

| Method/path | Contract |
| --- | --- |
| GET deals/{id}/bid-preparation/ | `opportunity`, `connection`, `connected`, `source_duration_months`, `requires_duration`, `capabilities:{can_create,can_attach,reason}`, `expected_token`. Reads never create work. |
| GET deals/{id}/bid-preparation-candidates/?search=&page= | `count,page,page_size,results:[{id,name}]`. Eligible unconnected Planning projects only. |
| POST deals/{id}/bid-preparation/ | `{request_id,expected_token,mode:'create'|'attach',reason,planning_project_id? ,duration_months?}`. Project ID applies only to attach; duration only to create and is required when opportunity duration is unknown. Returns `{bid_preparation,replayed}`. |
| GET quotes/{id}/preparation/ | `quote:{id,number,version,status,deal_id}`, `bid_preparation`, `connection`, `history`, `history_count`, `capabilities:{can_prepare,reason}`. Latest30 retained captures; each includes revision, source, selected_fields, reason, creator/time and source_state current/changed/unavailable. Inaccessible Planning sources are redacted. |
| GET quotes/{id}/preparation-sources/?search=&page= | Exact technical revisions within the bound workspace, with count/page/page_size/results. Each source identifies technical ID/revision/status, schedule version and exact generation; source links open existing Planning editors. |
| POST quotes/{id}/preparation-preview/ | `{technical_proposal_id}` returns `source,evidence,proposed_fields,current_fields,supported_fields,warnings,expected_token`. Permitted read even when Quote is frozen. |
| POST quotes/{id}/prepare/ | `{request_id,expected_token,technical_proposal_id,selected_fields,reason}` returns `{preparation,replayed}`. At least one distinct supported field required; reason nonempty/max1000. |

Supported imports are scope, deliverables, assumptions, exclusions, disciplines,
estimated_hours and risks. Missing/unsupported source values are omitted. Technical
sections/frozen snapshot supply solution evidence; current version-specific
resources/assignments and nonfinancial risk records are explicitly labelled
separately. Price, cost, tax, discount, currency and financial approval are never
imported. Employee identities and structured source costs are not exposed.

## Scope, state and retries

Current Sales opportunity and proposal read access plus Planning read/source
visibility is required. Connecting also requires opportunity update and Planning
create for a new workspace, or Planning update and writable selected project for
attach. Apply requires proposal update. A known organization relationship and
consistent canonical project/client scope are required; a connection grants no
access. Active client/new-proposal eligibility and the recorded approved bid
decision are enforced for new preparation work.

Actor-bound one-hour tokens bind the current opportunity, Quote and precise source
fingerprint. Commands lock actor (NO KEY UPDATE), Deal (NO KEY UPDATE), Planning
parent/source rows and Quote in that order. Current source access is checked
again on retries. An identical request has one recorded effect; changed payload,
stale source, changed capture result, and conflicting reuse return409. Validation
returns400, denied403 and unavailable404. Required capture/connection and opportunity
audit are atomic; any audit failure rolls back the update.

Generic Quote APIs retain identity, reject changes to governed evidence, and freeze
approved/submitted records. Existing HTTP route guards already protected approval
fields; serializer/domain validation additionally protects direct API reuse and
preparation identity. Commercial approval remains the configured existing route,
including its existing self-approval semantics.
Quote list/detail/mutations now use the existing Sales Deal visibility scope,
including known organization consistency; a preparation connection never widens it.
Technical review and PDF comments
remain independent. Pre-award detached Planning workspaces still need the existing
enterprise-project authority before final technical approval; this connection does
not manufacture that authority or alter the award-to-delivery lifecycle.

Verification evidence is recorded in the workspace feature brief. Focused tests:
`apps.sales.tests.test_bid_preparation`, Planning adapter tests and disposable
PostgreSQL concurrency/migration checks; no production deployment is implied.
