# Operational project controls

Release 3 adds **Operational controls** to the existing Master Schedule area selector. The sidebar, project navigation, schedule baseline, and employee task history retain their existing behavior.

## Reporting workflow

1. Publish the planning baseline and link the planning workspace to its enterprise project.
2. Open **Schedule → Master Schedule → Operational controls → Earning policy**. Select the earning method explicitly for each covered activity. Optional approved weights support overall physical progress. Monetary reporting additionally needs a stated currency, activity budgets and an approved planned-value method.
3. An independent project authority approves the policy. A policy containing monetary budgets additionally requires commercial approval authority. Approved policy definitions cannot be changed; create a new policy to change the basis.
4. Create a weekly report using the existing enterprise reporting period, or define a new period when no open period exists. Reporting periods are shared with Cost & Commercial; overlapping duplicate periods are rejected.
5. Record cumulative progress, actual dates, explicit remaining working days and a supporting report/document reference. Review **Source actuals**, save and submit the report.
6. An independent project authority publishes the reviewed observation. Authors, editors and the submitter cannot publish their own work. Late changes use **Create correction**, retaining the original publication.

Operational publication is not financial period locking. Reconciliation, labour posting and financial period approval remain in the existing Cost & Commercial workflow. Publishing operational observations never silently posts costs, sends employee messages, revises a baseline or changes contractual finish dates.

## Measurement and actuals

- Earning methods: manual physical percentage, 0/100, 50/50, and measured quantity divided by an approved quantity. An empty report row is unknown, including for binary earning methods.
- Weights, quantities, budgets, currency and time phasing are explicit policy inputs. Duration is not an earning weight. Planning profile stage weights are not silently interpreted as financial budgets or earning approvals.
- Planned value uses explicitly approved working-day linear phasing or cumulative dated monetary points. Explicit points are step observations without invented interpolation. Missing dates, calendars or budgets remain unavailable.
- Actual hours come from approved `project_control.ApprovedHourEntry` records. Biometric attendance is not evidence of project work. Hours do not automatically become physical progress or labour cost.
- Actual cost comes from posted `CostLedgerEntry` actual/adjustment records, as of the data date. Invoices and payments are not counted again. Unposted labour, invalid sources, mixed currencies and incomplete coverage prevent an unsupported AC/CPI/EAC.
- Exact WBS/activity bridges identify scope, but account totals are not divided across activities without approved allocations. This release exposes account/project source actuals; it does not manufacture activity cost allocations.
- No exchange rates or labour rates are assumed. Missing actuals remain null, not zero. Cost and rate visibility follows commercial access in addition to project access.

Monetary totals require complete approved activity budgets and measured progress. SPI = EV/PV; CPI = EV/AC. EAC = BAC × AC/EV uses unrounded inputs and requires positive AC/EV. Incomplete values include machine-readable absence reasons. Corrections and genuine gaps can be reported without pretending that a missing metric is valid.

## Forecasts and comparisons

The remaining-work forecast reads only the frozen baseline calendar/logic and the report's actual dates and explicit remaining durations. It is separate from planning CPM and never rewrites baseline activities. The report data date is end of day; unfinished work resumes on the next working day.

Supported forecast conventions are a single frozen whole-working-day calendar, working/nonworking exceptions, milestones, signed whole-day FS/SS/FF/SF relationships and supported date constraints. Missing remaining estimates or predecessor boundaries propagate unavailable results. Partial-day/mixed calendars and fractional day inputs are identified as unsupported rather than rounded or guessed.

Actual dates are retained, including out-of-sequence progress. Impossible constraints, negative forecast float and contractual finish overruns are warnings. The contractual finish remains the comparison target. Per-activity baseline/actual/forecast comparisons use exact frozen activity IDs, not similar names.

S-curves use published observations only. No historical EV or AC is synthesized from today's percentages. Corrections replace a period's point in the current series while the original report remains accessible. Different earning policies/currencies and missing points break the line.

## Integrity and API

`GET/POST /api/v1/planning-intelligence/projects/{project_id}/operational-controls/`

GET is read-only. Commands are `create_policy`, `approve_policy`, `create_report`, `save_report`, `submit_report`, `return_report`, `publish_report`, and `correction_report`. Commands enforce exact project/baseline/period identity, optimistic revisions, independent authority, evidence references and source fingerprints. Changed source actuals after submission require a new review. Published records capture the source manifest, policy definition, baseline fingerprint and calculation version.

Migration `0042_operational_controls` adds earning policies and operational reports. PostgreSQL triggers protect approved policies and published reports against ORM bulk updates/deletes. Existing baselines and business schedules receive no data rewrite.

The existing daily field update API also rejects activity/version rebinding, preventing a report from one project being redirected into another project through an edit.
