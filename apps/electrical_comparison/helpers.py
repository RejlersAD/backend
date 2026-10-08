"""
Shared comparison-row-building helpers — moved OUT of views.py so that
neither tasks.py nor views.py needs to import from the other.

BUG FIX: tasks.py used to lazy-import these three functions directly
from views.py, inside process_electrical_comparison()'s own function
body, AFTER several other lazy imports — all running BEFORE any
try/except in that function. Importing views.py pulls in its full
module-level import list (Django REST Framework views, DRF parsers,
the queue service, etc.) — if ANY of those failed inside a Celery
worker process (a different process/environment than the web server,
where an otherwise-harmless import issue can surface differently), the
whole task crashed in milliseconds, before job.status could ever be
set to 'failed' — the job just sat at 'processing' forever with no
error saved, or (worse) a generic exception's message happened to
contain a substring like 'api_key' and got mislabeled as "Invalid or
missing API key" by the error classifier, hiding the real cause
entirely.

Both views.py and tasks.py now import these three functions from HERE
instead of from each other — this file has no dependency on either of
them, so there is no import-order risk in either direction.
"""
from .models import ElectricalComparisonResult
from .services.tag_extractor import resolve_equipment_type, ELECTRICAL_TAG_PATTERN
from .services.excel_parser import PANEL_TYPE_CODES

# Equipment-List type codes that represent panel/switchgear equipment —
# same PANEL_TYPE_CODES set services/excel_parser.py uses to filter
# these OUT of Load List results (U, JB, BD, AB, TSG, SB).
_PANEL_TYPE_CODES = PANEL_TYPE_CODES


def _tag_type_code(tag: str) -> str:
    """area-type-sequence breakdown of a tag text — just the type_code
    segment, used to tell a panel-type Equipment List entry (U, SB) from
    a motor entry (PM, NPM, ...)."""
    m = ELECTRICAL_TAG_PATTERN.search((tag or '').upper())
    return m.group(2).upper() if m else ''


def _build_panel_verification(equipment_tags, load_list_tags, panel_info):
    """THE actual fix for the Equipment List vs Load List comparison:
    Equipment List holds panels (e.g. 285-U-505A, a SWITCHBOARD/MCC) and
    Load List holds motors (e.g. 285-PM-411B) — two different equipment
    classes. Matching those tags directly against each other (the old
    compare_with_equipment_list behavior here) produced a meaningless
    100%-"extra"/100%-"missing" result, since a motor tag can never
    equal a panel tag. The real question a Load List answers is "which
    panel are these motors fed from, and does that panel actually exist
    in the Equipment List?" — answered via the Load List PDF's own
    "PANEL TAG: ..." header line (see excel_parser.extract_panel_tag_from_load_list).

    equipment_tags/load_list_tags: [{'tag', 'description', ...}] from
    parse_excel_tags. panel_info: {'panel_tag', 'description'} or None.

    Returns {'panels': [...], 'motors': [...], 'counts': {...}}:
      panels[]: one row for the Load List's own panel (status
        'panel_verified' or 'panel_missing'), PLUS one row per OTHER
        panel-type (_PANEL_TYPE_CODES) Equipment List entry that has no
        Load List data of its own (status 'panel_no_load_list') — same
        shape as the spec's "Panel Coverage" table.
      motors[]: one row per Load List motor, tagged with which panel it
        belongs to and whether that panel was verified.
    """
    equipment_by_tag = {e['tag']: e for e in equipment_tags}
    load_panel_tag = panel_info['panel_tag'] if panel_info else None

    panels = []
    if load_panel_tag:
        eq_entry = equipment_by_tag.get(load_panel_tag)
        panels.append({
            'panel_tag': load_panel_tag,
            'description': (eq_entry.get('description') if eq_entry else '') or panel_info.get('description') or resolve_equipment_type(load_panel_tag),
            'in_equipment_list': eq_entry is not None,
            'motor_count': len(load_list_tags),
            'status': 'panel_verified' if eq_entry is not None else 'panel_missing',
        })

    # Every OTHER panel-type Equipment List entry — present in the
    # register, but this particular Load List upload has no motor data
    # for it (a different Load List document would cover it).
    for tag, entry in equipment_by_tag.items():
        if tag == load_panel_tag:
            continue
        if _tag_type_code(tag) not in _PANEL_TYPE_CODES:
            continue
        panels.append({
            'panel_tag': tag,
            'description': entry.get('description') or resolve_equipment_type(tag),
            'in_equipment_list': True,
            'motor_count': None,
            'status': 'panel_no_load_list',
        })

    panel_verified = bool(load_panel_tag and load_panel_tag in equipment_by_tag)
    motors = []
    for m in load_list_tags:
        motors.append({
            'motor_tag': m['tag'],
            'description': m.get('description', ''),
            'panel': load_panel_tag or '',
            'status': 'motor_verified' if panel_verified else 'motor_not_verified',
        })

    counts = {
        'panels_verified': sum(1 for p in panels if p['status'] == 'panel_verified'),
        'panels_missing': sum(1 for p in panels if p['status'] == 'panel_missing'),
        'total_motors': len(load_list_tags),
        'motors_verified': sum(1 for m in motors if m['status'] == 'motor_verified'),
    }
    return {'panels': panels, 'motors': motors, 'counts': counts}


def _collect_comparison_rows(job, results_to_save, comparison_result, source, primary_tags=None, reference_tags=None):
    """Appends every finding (mismatch/missing/extra/uncertain) from
    comparison_result.findings — AND (the actual fix this function was
    built for) a synthesized 'matched' row for every tag present on BOTH
    sides, since compare_with_equipment_list (apps.pid_verification_v2.
    services.comparison_engine) only ever increments matched_count for a
    clean match — it deliberately never appends a ComparisonFinding for
    one (that module was built to surface discrepancies, not a full
    matched/missing/extra audit trail). Electrical Comparison's results
    table needs the full picture, so the matched rows are rebuilt here
    from the same two tag lists the comparison itself was given.

    primary_tags/reference_tags: the exact two [{'tag': ...}, ...] lists
    passed into compare_with_equipment_list for this comparison — when
    given, matched rows are computed and appended BEFORE the
    findings-derived rows, so the table/export shows MATCHED tags first,
    then MISSING, then EXTRA. `results_to_save` is mutated in place.
    """
    # tag_extractor.py's _flag_suspicious_areas/_flag_suspicious_
    # sequences already compute 'suspicious'/'suspicious_reason' on each
    # P&ID-extracted tag dict, and tasks.py's pid_equip list carries
    # those two fields through — but compare_with_equipment_list only
    # ever reads 'tag'/'type' from its inputs, so those fields never
    # reach its ComparisonFinding output. Looked up here instead,
    # directly from primary_tags (the exact list passed into that
    # comparison — pid_equip for a P&ID comparison, plain Excel tags
    # with no such field for equipment_vs_loadlist, where
    # .get(..., False) safely defaults to "not suspicious").
    primary_by_tag = {t['tag']: t for t in (primary_tags or [])}

    if primary_tags is not None and reference_tags is not None:
        primary_tag_set = set(primary_by_tag.keys())
        reference_by_tag = {t['tag']: t for t in reference_tags}
        matched_tags = primary_tag_set & set(reference_by_tag.keys())
        for tag in sorted(matched_tags):
            ref_entry = reference_by_tag.get(tag, {})
            primary_entry = primary_by_tag.get(tag, {})
            # suspicious_reason already carries its own "⚠️ Needs Review —"
            # marker (see tag_extractor.py's _flag_suspicious_areas/
            # _flag_suspicious_sequences) — used as-is, not re-prefixed.
            remarks = primary_entry.get('suspicious_reason', '') if primary_entry.get('suspicious') else ''
            results_to_save.append(ElectricalComparisonResult(
                job=job,
                tag_number=tag,
                description=ref_entry.get('description', ''),
                status='matched',
                source=source,
                equipment_type=resolve_equipment_type(tag),
                remarks=remarks,
            ))

    for item in getattr(comparison_result, 'findings', []):
        tag_number = getattr(item, 'item_id', '')
        evidence = getattr(item, 'evidence', '')
        primary_entry = primary_by_tag.get(tag_number, {})
        remarks = evidence
        if primary_entry.get('suspicious'):
            # suspicious_reason already carries its own "⚠️ Needs
            # Review —" marker — used as-is, not re-prefixed.
            needs_review = primary_entry.get('suspicious_reason', '')
            remarks = f"{needs_review} | {evidence}" if evidence else needs_review
        results_to_save.append(
            ElectricalComparisonResult(
                job=job,
                tag_number=tag_number,
                description=getattr(item, 'issue_observed', ''),
                status=getattr(item, 'category', ''),
                source=source,
                # BUG FIX: the model has an equipment_type column the
                # frontend's results table actually displays, but the
                # comparison engine's ComparisonFinding has no such
                # field — it was never being set here, so every row
                # showed blank. Resolved straight from the tag text via
                # the same electrical legend lookup used during
                # extraction, so it works for every source (P&ID-
                # extracted, Excel-only, missing).
                equipment_type=resolve_equipment_type(tag_number),
                remarks=remarks,
            )
        )


def _build_combined_comparison(pid_tags, equipment_tags, load_list_tags):
    """Tab 4 ("Full Comparison") — the union of all 3 sources' tags,
    each flagged for exactly which source(s) it actually appears in.
    Only ever called (by tasks.process_electrical_comparison) when ALL
    THREE files were uploaded.

    pid_tags: extract_electrical_tags()'s own 'tags' list
    ({'tag', 'equipment_type', ...} — no 'description' field, since the
    P&ID extraction never produces one).
    equipment_tags/load_list_tags: parse_excel_tags() output
    ({'tag', 'description', ...}).

    Never guesses: in_pid/in_equipment/in_load_list are each computed by
    a direct set-membership check against that ONE source's own tag
    set — never inferred from another source's presence/absence.

    Status per tag:
      fully_matched  — present in all 3 sources
      partial        — present in exactly 2 of 3
      single_source  — present in only 1

    Returns {'rows': [...], 'counts': {...}}. Each row:
      {'tag', 'description', 'in_pid', 'in_equipment', 'in_load_list',
       'status', 'remarks'}
    """
    pid_tag_set = {t['tag'] for t in pid_tags}
    equipment_tag_set = {t['tag'] for t in equipment_tags}
    load_list_tag_set = {t['tag'] for t in load_list_tags}

    combined_tags = pid_tag_set | equipment_tag_set | load_list_tag_set

    # Description lookup — Equipment List first, Load List as fallback
    # only when a tag isn't already in the dict (NOT merely falsy/empty
    # — an explicit blank Equipment List description is still "from
    # Equipment List" and must not be silently overridden by Load
    # List). A P&ID-only tag has no entry in either Excel source, so it
    # naturally falls through to '' below — P&ID extraction itself never
    # produces a description.
    desc_lookup = {}
    for t in equipment_tags:
        desc_lookup[t['tag']] = t.get('description', '')
    for t in load_list_tags:
        if t['tag'] not in desc_lookup:
            desc_lookup[t['tag']] = t.get('description', '')

    rows = []
    for tag in sorted(combined_tags):
        in_pid = tag in pid_tag_set
        in_equipment = tag in equipment_tag_set
        in_load_list = tag in load_list_tag_set
        true_count = sum([in_pid, in_equipment, in_load_list])

        if true_count == 3:
            tag_status = 'fully_matched'
        elif true_count == 2:
            tag_status = 'partial'
        else:
            tag_status = 'single_source'

        description = desc_lookup.get(tag, '')
        sources_present = []
        if in_pid:
            sources_present.append('P&ID')
        if in_equipment:
            sources_present.append('Equipment List')
        if in_load_list:
            sources_present.append('Load List')

        rows.append({
            'tag': tag,
            'description': description,
            'in_pid': in_pid,
            'in_equipment': in_equipment,
            'in_load_list': in_load_list,
            'status': tag_status,
            'remarks': f"Found in: {', '.join(sources_present)}",
        })

    counts = {
        'fully_matched': sum(1 for r in rows if r['status'] == 'fully_matched'),
        'partial': sum(1 for r in rows if r['status'] == 'partial'),
        'single_source': sum(1 for r in rows if r['status'] == 'single_source'),
        'total': len(rows),
    }
    return {'rows': rows, 'counts': counts}
