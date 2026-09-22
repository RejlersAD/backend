# Identity and source linking

The `exact-source-identity-v1` boundary separates source occurrences from
deliverable identity decisions. It applies to new document plans, source timing
association, schedule-basis rows, and source-based draft reconciliation.

## Rules now enforced

- Identifiers are opaque strings. Case, punctuation, whitespace and leading
  zeros are not removed for identifier lookup. Titles and aliases never identify
  another document's deliverable or supply its dates and durations.
- A source identity includes the project, file, available version/hash,
  namespace or sheet, and revision. Newly extracted timing references capture
  the SHA-256 of the UTF-8 extracted text. That hash is explicitly separate from
  the original file's integrity hash.
- The same identifier in another project, revision or namespace is not an
  established link. A duplicate identifier in the same scope creates a review
  candidate. Both assertions remain present, including conflicting values.
- Every schedule-basis source fact creates its own deliverable record, including
  repeated register rows and identical names. Legacy routes use the same rule;
  approximate matching and automatic canonical-name merging were removed.
- Retrying extraction can deduplicate the same assertion at the same source
  offsets and text version. It cannot deduplicate separate physical occurrences.
- Timing is associated only through an existing exact source identifier or
  physical source locator in the same source version. A source pointer without
  a version does not silently acquire the current version. Missing identity
  becomes an actionable issue, not a guessed title match.
- Draft reconciliation collision-checks both old and new occurrences. It does
  not pick the last employee assignment from a dictionary of duplicate keys or
  transfer accepted values to a changed source revision.
- Scalar basis inputs are not selected using confidence or global document
  priority. Extracted values require a recorded acceptance. Conflicting or
  unreviewed values remain missing with readiness issues; merely closing a
  conflict label does not change an existing basis snapshot.

## Register and schedule scope

`build_document_plan` exposes `deliverables` and `register_inventory` separately
from extracted schedule `activities`. When a register is available,
`scope_authority` is `source_register`. A schedule cannot replace that register
merely because it contains more timing detail.

`scope_activity_ids` contains only activities associated with the same physical
source row. Other associations remain in
`unmapped_source_schedule_activity_ids`, with blocking review issues. Same-named
items across documents are deliberately not joined. Explicit predecessor
references retain their relationship type and lag unit, and remain scoped to
their source namespace/version.

`evidence_entity_id` preserves the original graph entity through draft and
relational materialization. It is restored from server-held task evidence on
save, rather than accepted from a client assertion. An ID is a navigation key,
not evidence of approval.

## Compatibility and remaining limits

### Rebuilding a document draft

The Schedule actions menu exposes **Rebuild draft from inputs** for editable
document drafts. A single confirmation uses the draft revision the user
reviewed; a concurrent edit causes a revision conflict instead of replacement.

Rebuild compares newly extracted parent occurrences with the saved parent
records. A unique match in the same document version retains the existing
workflow stage IDs, employee assignments and live progress. Stage citations
are never used as competing parent matches. Changed, missing-version or
ambiguous occurrences become fresh, unassigned source activities; rebuild
does not automatically expand a workflow template. Changing a file's content
hash changes its occurrence identities even if some titles remain the same.

Replaced employee tasks are withdrawn from active work, while their status,
progress and assignment history remain in the audit records. Dependencies to
removed tasks are removed and flagged for sequence review, without connecting
them to similarly named replacements. Prior proposal tokens, calculation and
pending review state are invalidated. Existing published baselines are not
modified; start a revision before rebuilding a published plan. If extraction
recovers no supported activities, the rebuild fails atomically and keeps the
current draft and assignments. The same protection applies when an existing,
still-attached scope source yields no activities while other files still parse,
or when all reference documents have been removed.

Retaining a parent's MDR identity does not validate its old schedule evidence.
Rebuild checks captured text hashes on secondary timing and relationship
citations against the current files. Obsolete or unversioned evidence moves to
`source_evidence_history`; unsupported source timing and source links are
cleared and flagged for review. Explicit planner values and workflow links
remain intact, along with employee assignments and progress.

Existing project data is not rewritten by this upgrade. Previously saved source
dates remain historical evidence. A new reconciliation can require a reviewed
link where an older version relied on names or lacked version metadata.

The source-timing adapter does not itself authorize cross-document mappings or
consume client-provided approval booleans. Authorized link decisions belong to
the evidence graph and must be resolved there before downstream planning uses
them. An accepted graph link does not automatically merge records or overwrite
existing source values.

Legacy vocabulary/discipline classifiers still exist as explicitly opted-in
compatibility helpers. The live document-driven analysis and fact-extraction
entry points do not invoke them. Topic mentions, acronyms and study names cannot
populate required scope. Explicit register rows remain intact, narrative
requirements remain quoted assertions, and located AI extraction remains
reviewable. Supported printed-table adapters continue to extract source facts.
None of these extraction results is identity approval, proof of a contractual
obligation, or an approved dependency by itself.
Unrecognized layouts require extraction review; the identity boundary does not
claim complete document understanding or universal parsing coverage.

No migration is required specifically for the identity helper: additional
provenance and review metadata use existing JSON fields. The evidence graph has
its own explicit schema migrations and approval controls.
