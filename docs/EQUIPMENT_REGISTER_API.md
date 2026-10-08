# Equipment Register draft API

Implemented 7 October 2026 for the Process Equipment List Phase 1 persistence
slice. These routes are owned by `pid_analysis`, require authentication and are
project-scoped through the existing Project Organizer access policy.

## Read the current register

`GET /api/v1/pid/equipment-registers/current/?project_id=<uuid>`

Returns HTTP 200 with the active register, current revision and rows. Returns
HTTP 204 when the accessible project has no register. A non-member receives the
existing project-access denial.

## Import an extraction as a draft revision

`POST /api/v1/pid/equipment-registers/import-extraction/`

Required fields are `project_id`, a stable `source_upload_id`, and a non-empty
`items` array. `source_files`, `drawing_ref`, and `extraction_mode` are optional
source metadata. Tags are required and case-insensitively unique within the
revision; at most 5,000 rows are accepted.

The command is atomic. A first import returns 201 and creates a new draft
revision. Retrying the current source upload returns 200 without duplicating it.
A previous draft becomes immutable and superseded. Submitted and approved
revisions remain unchanged; a later extraction becomes a separate draft.
Retrying an upload that belongs to an older revision returns 409
`source_upload_already_imported` and does not move the register backwards.

## Edit one draft row

`PATCH /api/v1/pid/equipment-registers/<register_uuid>/items/<item_uuid>/`

Request shape:

```json
{
  "expected_revision_version": 1,
  "set": {"description": "Reviewed separator", "design_pressure_max": "180"},
  "reason": "Checked against the source drawing."
}
```

Only the current draft revision can change. Each successful material update
records field-level before/after evidence and increments `revision.version`.
An outdated version returns HTTP 409 with code `stale_revision` and the current
version. Submitted, approved, superseded, or otherwise immutable revisions
return 409 without mutation. Unknown or inaccessible objects are not exposed.
The register, current revision, and item are locked independently so PostgreSQL
does not attempt `FOR UPDATE` through the register's nullable current-revision
outer join.

## Read current-revision change evidence

`GET /api/v1/pid/equipment-registers/<register_uuid>/changes/`

Returns the current revision's extraction and manual field-change evidence in
reverse chronological order. Optional `item_id=<uuid>` limits the response to
one item. The response includes the equipment tag, field, before/after values,
source, reason, actual actor name and timestamp. Results are bounded to 1,000
events and report `total` and `truncated`; project access is rechecked.

Current-register rows expose a derived presentation status (`Changed`,
`Reviewed`, `Warnings`, or `Unchanged`) and the revision response includes
changed/reviewed/discrepancy/unreviewed counts. These are evidence projections,
not revision approval or engineering validation decisions.

## Structured source metadata

Items expose a bounded `metadata` object containing `pid_information`,
`equipment_record`, `engineering_specifications`,
`connected_safety_equipment`, `main_process_instruments`, `connected_lines`,
`validation_findings`, `confidence`, and `field_evidence`. Unknown sections,
invalid shapes, confidence outside 0-100, and values over the configured bounds
return HTTP 400. If import metadata is absent, the server projects the source
P&ID and existing flat row fields. Flat-field draft edits refresh matching
metadata values while preserving richer extracted evidence.

## Equipment Master projection

Every item read also exposes `equipment_master` (schema version `1.0`), a
structured read projection of the persisted `metadata`. The legacy flat item
fields and `metadata` remain compatible. New extractions can include:

- `attributes`: bounded engineering attribute keys, each with `value`, `unit`
  and an exact source `evidence` excerpt where available;
- `relationships`: arrays keyed by `process_lines`, `instruments`,
  `control_valves`, `shutdown_valves`, `safety_valves`, `alarms`, `trips`,
  `internals`, `engineering_notes`, `process_streams`, `connected_equipment`,
  and `cross_pid_references`. Each edge contains its tag/description, source
  `drawing_no`, `filename`, `page` when known, verbatim `evidence`, optional
  confidence (0-100), and `review_state=proposed`;
- `source_documents`: source drawing, filename, revision and page references.

### Provenance, traceability and coverage

Relationship entries additionally carry:

- `source`: `text` (OCR/vector citation) or `vision` (AI vision pass). Absent
  means `text`.
- `bbox`: optional source region `[x, y, w, h]` in PDF points when known.
- `resolution`: batch cross-reference state for cross-drawing edges —
  `resolved_in_batch` (the referenced drawing or related tag was part of the
  same extraction upload, with `resolved_drawing_no`, `resolved_filename`,
  `resolved_page` locating it) or `unresolved`. Blank when no batch check ran
  (single-file extraction) or the edge is same-drawing.

The projection also reports `extraction_coverage` (per-group `count`, `found`,
`sources`, plus `groups_found`, `groups_total` and a `completeness` ratio) and
`provenance` counts (`text` vs `vision`). These are extraction estimates: a
blank group means unverified, not absent.

### Deterministic drawing-wide harvesters

Beyond same-line tag + link-keyword detection, the extraction baseline reads
structured drawing content where the relationship is stated explicitly by the
document structure (never by text proximity alone):

- note blocks: `SEE NOTE n` citations naming the equipment tag are resolved
  against `NOTE n:` definitions and attached to `engineering_notes` (alarm/trip
  tags named by the note are linked into `alarms`/`trips`). Equipment-scoped
  notes blocks (`V-805-TF NOTES:`, `NOTES FOR V-805-TF` or a bare tag line)
  contribute their content lines: recognized tags join their groups,
  mechanical-feature lines join `internals`, remaining text joins
  `engineering_notes`;
- on-drawing line lists: rows in a detected line-list section that name the
  equipment tag contribute the line to `process_lines` (with FROM/TO
  direction) and counterparty equipment to `connected_equipment`;
- schedule sections: rows under headers such as `PSV SCHEDULE`,
  `INSTRUMENT LIST`, `VALVE LIST`, `ALARM LIST` or `LINE LIST` that name the
  equipment tag contribute every recognized tag on the row to its group
  (instruments, control/shutdown/safety valves, alarms, trips), with the
  remaining row text (e.g. `SET PRESSURE: 600 PSIG CASE: FIRE`) as the
  description;
- connector / tie-in / continuation sections: rows in a detected connector
  section that is demonstrably about the equipment (a header or row naming
  the tag) attach drawing-number tokens to `cross_pid_references` and remote
  equipment tags to `connected_equipment`; rows not naming the tag require a
  boundary keyword (UPSTREAM/DOWNSTREAM/SOURCE/FEED/DRAIN/FLARE/...);
- data-box labels: labeled attributes beyond the flat register fields
  (internals, trim, lining/coating, insulation type, weights, capacity,
  elevation, driver) populate `attributes` with the verbatim source line.

Tag recognition covers the ISA-style families used on real P&IDs, including
DP and X instrument families, pressure gauges, loop-letter variants
(`PI-8002L`) and train/unit suffixes (`PT-8001A-TF`); MOVs classify as
control valves. Total relationship entries per item are capped
(`equipment_relationship_total_max`, default 45) so the harvested evidence
always fits the 64 KiB metadata envelope — overflow keeps the first entries
and records a review warning instead of forfeiting every link. Everything is
soft-coded under `extraction` in
`apps/pid_analysis/config/equipment_type_config.json`: harvester enable
switches (`equipment_notes_enabled`, `equipment_notes_blocks_enabled`,
`equipment_line_list_enabled`, `equipment_schedule_sections_enabled`,
`equipment_connector_sections_enabled`,
`equipment_databox_attributes_enabled`), limits (`equipment_notes_max`,
`line_list_min_rows`, `equipment_databox_attributes_max`) and patterns
(`line_list_line_tag_pattern`, `schedule_section_header_pattern`,
`connector_section_header_pattern`, `connector_boundary_keywords`,
`notes_block_keywords`, `mechanical_keywords`, `drawing_number_pattern`,
`relationship_tag_patterns`).

### Optional AI vision pass

For connections that exist only graphically, the extractor can render P&ID
pages to images and ask a vision model (`MultiModelAIService.vision_analysis`)
for visibly connected lines, instruments, valves, alarms/trips, internals,
notes, streams and equipment. Controls (`equipment_vision_enabled`,
`equipment_vision_max_pages`, `equipment_vision_dpi`,
`equipment_vision_tags_per_request`, `equipment_vision_max_tokens`,
`equipment_vision_text_confidence_cap`) live in the same config. The pass is
skipped (with a `validation_findings` warning) when disabled or unconfigured,
and never breaks extraction. Anti-hallucination: a proposed related tag must
appear in the page text layer unless the group is description-only
(`engineering_notes`/`internals`/`process_streams`); entries with evidence not
verbatim in the text keep a capped confidence. All accepted output stays
`review_state=proposed` with `source=vision`.

### Batch cross-P&ID resolution

Multi-file extraction additionally resolves cross-references within the same
upload batch: `cross_pid_references` whose referenced drawing number matches
another file's drawing number become `resolved_in_batch` (with locator);
`connected_equipment`/`process_lines` edges whose related tag was extracted
from another file in the batch are stamped the same way. Batches with fewer
than two distinct source drawings are unchanged. Resolution is an observation
about the upload, not an authorized link to a controlled document.

The alias supplies empty relationship groups for pre-existing records without
rewriting historical snapshots. A reference to another P&ID identifies a
source-observed continuation, not a verified link to a separately controlled
document or a foreign-project record. A missing edge does not prove that no
physical connection exists. Quoted/AI-proposed links require engineer review;
confidence is an extraction estimate, not a calibrated safety assurance.

The importer validates group names, shapes, sizes, evidence and confidence.
The existing 64-KiB per-item metadata limit and 5,000-row import limit remain.
There is no new schema migration: the existing JSON field carries additive
fields; old clients still read and edit the same flat columns. The Equipment
List UI also exports the loaded proposal as Equipment Master JSON alongside
its existing Excel exports.

## Deliberate limits

These routes do not submit, approve, issue, archive, delete, or synchronize a
register. They do not infer an approver or engineering validation rule. Excel
export remains a client-side export of the loaded authoritative revision.
