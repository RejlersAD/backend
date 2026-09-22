# Agreement to project workspace

An agreement can start a new project or supply a draft for an existing project. Upload it once with **Analyze & set up project**. The response returns a durable background job immediately. Reopening the project resumes progress through a read-only GET. After analysis, **Accept supported inputs & build draft** accepts the unambiguous document facts together; grouped exceptions remain available for review.

The same versioned agreement assertions appear in Overview, Schedule, Cost & Commercial, Milestones, Risks & Changes, Estimates, Documents, and Activity & Audit. Each assertion retains its original file hash, extracted-text hash, physical page, verbatim excerpt and character offsets. AI proposals and calculated values have distinct meanings. Uploaded document content is data, never an instruction to the assistant or application.

## What acceptance changes

- Fills empty project scope, client, project name and explicit commencement date when permissions permit. Existing values and approved schedules are preserved.
- Records the stated contract value and currency separately from the internal cost budget, actual costs and earned-value reporting.
- Creates explicitly dated milestones where no matching milestone exists. Relative milestones keep their amount, unit and starting event in the shared draft.
- Seeds an empty planning draft from accepted deliverables. Unstated activity durations, calendars and relationships remain missing.
- Records analysis and acceptance events with source references and the user's conflict-resolution reason.

The agreement alone does not establish BAC, actual progress, CPI, SPI, approved changes, achieved milestones or a CPM network. Eight months from commencement and 28 weeks from award remain separate requirements. They do not silently become a single project finish date. Working calendars, activity durations and predecessor relationships must be supplied and validated by the existing deterministic scheduling engine before dates and float are calculated.

## API

Routes are under `/api/v1/planning-intelligence/agreement-workspaces/`:

| Method and route | Behavior |
| --- | --- |
| `GET projects/{enterprise_project_id}/` | Latest draft, permissions, uploaded sources, project AI availability, active and latest jobs; does not create records. |
| `POST create/` | Multipart `file`, UUID `idempotency_key`, optional `name`, `code`, `ai_api_key`, `ai_model`; creates project, upload and analysis job atomically. |
| `POST projects/{enterprise_project_id}/analyze/` | Multipart `file` or JSON `file_ids`, plus UUID `idempotency_key`; queues analysis of this project's sources. |
| `POST projects/{enterprise_project_id}/accept/` | `workspace_id`, `revision`, optional `reason` and `selected_fact_ids`; accepts supported facts. Explicit conflict choices require a reason. |

Successful analysis requests return HTTP 202 with the job and workspace envelope. Repeating an identical request key reuses the job; reusing a key with different inputs returns 409. A running analysis prevents competing writes to its agreement draft. Analysis and acceptance recheck project access and source identity, and acceptance verifies original bytes. New analysis creates a version; immutable source assertions cannot be edited through acceptance, ORM saves or PostgreSQL bulk updates.

## Parsing and AI

Analysis uses only the project's configured Anthropic connection. A new project can receive an encrypted project-specific key during creation. Keys are excluded from responses, job payloads and audit events. Missing or unavailable AI produces explicit coverage warnings and conservative source extraction; it does not report a successful complete AI review.

Scanned PDF pages with electronic-signature footers are OCR candidates even when they contain some machine-readable text. OCR removes long table rules from its temporary image to recover obscured text; the uploaded file is unchanged. Page, text, chunk and response limits are reported through extraction coverage. Analysis never claims that processing every page proves semantic completeness.

Run migration `0047_agreement_workspace` and restart web and Celery workers when deploying. It adds the agreement workspace table and source-immutability trigger, the agreement upload category and the background job type; it does not rewrite existing schedules or financial records.
