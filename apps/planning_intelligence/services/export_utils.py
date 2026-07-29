"""
Export Service (MODULE 12) — CSV / JSON / Primavera-ready CSV / Excel / PowerPoint.

CSV & JSON are the MVP-required minimum bar per spec. Excel, a
Primavera-compatible column layout, and a client-ready PowerPoint deck are
provided as they only need libraries already in requirements.txt (openpyxl,
python-pptx) — no extra dependency risk beyond python-pptx itself.
"""
from __future__ import annotations

import csv
import datetime
import io
import json
import os
import re

from django.utils import timezone

from ..config import DEFAULT_CALENDAR, PRIMAVERA_XER_VERSION
from .calendar_utils import add_working_days

ACTIVITY_COLUMNS = [
    'id', 'wbs_code', 'name', 'discipline', 'deliverable', 'responsible_role',
    'original_duration_days', 'start_date', 'finish_date', 'total_float_days',
    'is_critical', 'is_milestone',
]

PRIMAVERA_COLUMNS = [
    ('id', 'Activity ID'),
    ('name', 'Activity Name'),
    ('original_duration_days', 'Original Duration'),
    ('start_date', 'Start'),
    ('finish_date', 'Finish'),
    ('total_float_days', 'Total Float'),
    ('predecessors_str', 'Predecessors'),
]

# ─────────────────────────────────────────────────────────────────────────
# PowerPoint export — built on Rejlers' own corporate template so exported
# decks carry the real brand (fonts, colors, logo, master slides) instead of
# a hand-drawn approximation. Template file is a versioned asset shipped with
# the backend (COPY'd into every Docker image — local & production alike),
# so no extra deploy step or file path configuration is required.
# ─────────────────────────────────────────────────────────────────────────
PPTX_TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), '..', 'assets', 'rejlers_template.pptx')

# Layout names inside rejlers_template.pptx — soft-coded so a template
# revision only needs a one-line update here (not a rewrite of the slide
# building logic) if Rejlers ever renames a layout in the master.
PPTX_LAYOUT_COVER = 'Start slide'
PPTX_LAYOUT_CONTENT = 'Text slide (light grey)'
PPTX_LAYOUT_TABLE = 'Graph/table slide (light grey)'
PPTX_LAYOUT_CLOSING = 'Last slide_Thanks'

# Placeholder indexes used by the above layouts (verified against the
# template — see idx values on `layout.placeholders`).
PPTX_HEADING_PLACEHOLDER_IDX = 12   # present on every content layout — slide heading
PPTX_BODY_PLACEHOLDER_IDX = 14      # "Text slide (light grey)" — bullet/body text area
PPTX_CONTENT_PLACEHOLDER_IDX = 15   # "Graph/table slide (light grey)" — table/graphic area

PPTX_MAX_TABLE_ROWS = 12  # keep slide tables readable — soft-coded cap



def _predecessors_str(activity: dict) -> str:
    return ', '.join(p['id'] for p in activity.get('predecessors', []) if p.get('id'))


def activities_to_csv(activities: list) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ACTIVITY_COLUMNS, extrasaction='ignore')
    writer.writeheader()
    for a in activities:
        writer.writerow(a)
    return buf.getvalue()


def activities_to_primavera_csv(activities: list) -> str:
    buf = io.StringIO()
    headers = [label for _, label in PRIMAVERA_COLUMNS]
    writer = csv.writer(buf)
    writer.writerow(headers)
    for a in activities:
        row = dict(a)
        row['predecessors_str'] = _predecessors_str(a)
        writer.writerow([row.get(key, '') for key, _ in PRIMAVERA_COLUMNS])
    return buf.getvalue()


def eddr_to_csv(eddr: list) -> str:
    if not eddr:
        return ''
    fieldnames = sorted({key for row in eddr for key in row.keys()})
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(eddr)
    return buf.getvalue()


def generation_to_json(generation) -> str:
    return json.dumps({
        'project': generation.project.name,
        'version': generation.version,
        'intelligence': generation.intelligence,
        'wbs': generation.wbs,
        'activities': generation.activities,
        'logic_matrix': generation.logic_matrix,
        'eddr': generation.eddr,
        'milestones': generation.milestones,
        'manhours': generation.manhours,
        'validation': generation.validation,
        'narrative': generation.narrative,
    }, indent=2, default=str)


def activities_to_excel_bytes(activities: list) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'Activities'
    ws.append(ACTIVITY_COLUMNS)
    for a in activities:
        ws.append([a.get(col, '') for col in ACTIVITY_COLUMNS])

    stream = io.BytesIO()
    wb.save(stream)
    return stream.getvalue()


def _pptx_get_layout(prs, name):
    """Look up a slide layout by its name in the Rejlers template. Falls back
    to the deck's first layout so export never hard-fails if the template is
    ever revised/renamed."""
    for layout in prs.slide_layouts:
        if layout.name == name:
            return layout
    return prs.slide_layouts[0]


def _pptx_strip_sample_slides(prs):
    """rejlers_template.pptx ships with sample/demo slides (used internally by
    Rejlers to showcase the template). Remove them so generated decks start
    clean while keeping every master/layout/theme/logo asset intact."""
    from pptx.oxml.ns import qn

    slide_id_list = prs.slides._sldIdLst
    for sld in list(slide_id_list):
        r_id = sld.get(qn('r:id'))
        prs.part.drop_rel(r_id)
        slide_id_list.remove(sld)


def _pptx_add_cover_slide(prs, project, generation):
    slide = prs.slides.add_slide(_pptx_get_layout(prs, PPTX_LAYOUT_COVER))
    tf = slide.placeholders[PPTX_HEADING_PLACEHOLDER_IDX].text_frame
    tf.word_wrap = True
    tf.paragraphs[0].text = project.name or 'Untitled Planning Project'

    subtitle_bits = [b for b in [project.phase, project.client, project.location] if b]
    for line in (
        ' · '.join(subtitle_bits) if subtitle_bits else 'Project Planning Presentation',
        f'Effective Date: {project.effective_date or "—"}',
        f'RADAI Project Planning Application · Schedule v{generation.version}',
    ):
        p = tf.add_paragraph()
        p.text = line
    return slide


def _pptx_add_bullet_slide(prs, heading, bullets, max_items=10):
    slide = prs.slides.add_slide(_pptx_get_layout(prs, PPTX_LAYOUT_CONTENT))
    slide.placeholders[PPTX_HEADING_PLACEHOLDER_IDX].text = heading

    tf = slide.placeholders[PPTX_BODY_PLACEHOLDER_IDX].text_frame
    tf.word_wrap = True
    items = bullets[:max_items] or ['No data available.']
    for i, text in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = str(text)
    if len(bullets) > max_items:
        p = tf.add_paragraph()
        p.text = f'…and {len(bullets) - max_items} more (see full export for details).'
    return slide


def _pptx_add_table_slide(prs, heading, headers, rows, max_rows=PPTX_MAX_TABLE_ROWS, footnote=None):
    """Adds a table-content slide using the template's 'Graph/table slide'
    layout. The layout's empty content placeholder only supports simple text
    (it is not a native table placeholder in python-pptx), so we read its
    on-brand position/size then swap it for a real table at that exact spot —
    keeping the table perfectly aligned with the template's grid."""
    from pptx.util import Pt, Emu

    slide = prs.slides.add_slide(_pptx_get_layout(prs, PPTX_LAYOUT_TABLE))
    slide.placeholders[PPTX_HEADING_PLACEHOLDER_IDX].text = heading

    content_ph = slide.placeholders[PPTX_CONTENT_PLACEHOLDER_IDX]
    left, top, width, height = content_ph.left, content_ph.top, content_ph.width, content_ph.height
    content_ph._element.getparent().remove(content_ph._element)

    note_lines = []
    if len(rows) > max_rows:
        note_lines.append(f'Showing {max_rows} of {len(rows)} rows — full detail available via CSV/Excel export.')
    if footnote:
        note_lines.append(footnote)
    table_height = Emu(int(height * 0.85)) if note_lines else height

    display_rows = rows[:max_rows]
    graphic_frame = slide.shapes.add_table(len(display_rows) + 1, len(headers), left, top, width, table_height)
    table = graphic_frame.table
    for c, header in enumerate(headers):
        table.cell(0, c).text = str(header)
        for run in table.cell(0, c).text_frame.paragraphs[0].runs:
            run.font.bold = True
    for r, row in enumerate(display_rows, start=1):
        for c, value in enumerate(row):
            table.cell(r, c).text = '' if value is None else str(value)

    if note_lines:
        note_top = Emu(int(top) + int(table_height) + Emu(int(prs.slide_height * 0.015)))
        note_box = slide.shapes.add_textbox(left, note_top, width, Emu(int(prs.slide_height * 0.08)))
        ntf = note_box.text_frame
        ntf.word_wrap = True
        for i, line in enumerate(note_lines):
            p = ntf.paragraphs[0] if i == 0 else ntf.add_paragraph()
            run = p.add_run()
            run.text = line
            run.font.size = Pt(11)
            run.font.italic = True
    return slide


def _pptx_add_closing_slide(prs, message='Thank You'):
    slide = prs.slides.add_slide(_pptx_get_layout(prs, PPTX_LAYOUT_CLOSING))
    slide.placeholders[PPTX_HEADING_PLACEHOLDER_IDX].text = message
    return slide


def generation_to_pptx_bytes(generation) -> bytes:
    """
    Builds a client/internal-review-ready PowerPoint deck summarizing a
    PlanningGeneration, using Rejlers' own corporate template
    (assets/rejlers_template.pptx) for the slide masters/layouts — cover,
    agenda, content, table and closing slides all inherit that template's
    fonts, colors and logo placement automatically.
    """
    from pptx import Presentation

    project = generation.project
    activities = generation.activities or []
    wbs = generation.wbs or []
    eddr = generation.eddr or []
    manhours = generation.manhours or {}
    validation = generation.validation or []
    intelligence = generation.intelligence or {}

    try:
        prs = Presentation(PPTX_TEMPLATE_PATH)
        _pptx_strip_sample_slides(prs)
    except Exception:
        # Defensive fallback so export never hard-fails if the template asset
        # is ever missing/unreadable — falls back to a blank deck.
        prs = Presentation()

    # 1) Cover
    _pptx_add_cover_slide(prs, project, generation)

    # 2) Agenda
    milestones = generation.milestones or [a for a in activities if a.get('is_milestone')]
    agenda_items = ['Project Overview', 'Work Breakdown Structure', 'Schedule Summary']
    if milestones:
        agenda_items.append('Key Milestones')
    agenda_items += [
        'Engineering Document Deliverable Register', 'Manhour Estimate',
        'Validation & Quality Checks', 'Executive Summary',
    ]
    _pptx_add_bullet_slide(prs, 'Agenda', agenda_items, max_items=len(agenda_items))

    # 3) Project Overview
    overview_bullets = [
        f'Client: {project.client or "—"}',
        f'Location: {project.location or "—"}',
        f'Phase: {project.phase or "—"}',
        f'Effective Date: {project.effective_date or "—"}',
        f'Planned Duration: {project.duration_months} month(s)',
        f'Reference Documents Analyzed: {len(intelligence.get("disciplines", {}) or {})} discipline(s)',
        f'Schedule Version: v{generation.version}',
    ]
    _pptx_add_bullet_slide(prs, 'Project Overview', overview_bullets)

    # 4) WBS Summary
    top_level = [n for n in wbs if n.get('level') == 1] or wbs
    wbs_bullets = [f'{n.get("code", "")} — {n.get("name", "")}' for n in top_level]
    if not wbs_bullets:
        wbs_bullets = ['WBS not yet generated.']
    else:
        wbs_bullets.insert(0, f'{len(wbs)} total WBS node(s) across the project.')
    _pptx_add_bullet_slide(prs, 'Work Breakdown Structure', wbs_bullets)

    # 5) Schedule Summary
    critical = [a for a in activities if a.get('is_critical')]
    finish_dates = [a.get('finish_date') for a in activities if a.get('finish_date')]
    schedule_bullets = [
        f'Total Activities: {len(activities)}',
        f'Critical Path Activities: {len(critical)}',
        f'Milestones: {len(milestones)}',
        f'Projected Finish: {max(finish_dates) if finish_dates else "—"}',
    ]
    _pptx_add_bullet_slide(prs, 'Schedule Summary', schedule_bullets, max_items=6)

    # 6) Key Milestones (only when milestones exist)
    if milestones:
        _pptx_add_table_slide(
            prs, 'Key Milestones',
            ['Milestone', 'Finish Date'],
            [[m.get('name', ''), m.get('finish_date', '')] for m in milestones],
            max_rows=8,
        )

    # 7) EDDR Summary
    if eddr:
        _pptx_add_table_slide(
            prs, 'Engineering Document Deliverable Register',
            ['Discipline', 'Deliverable', 'Final Issue'],
            [[row.get('discipline', ''), row.get('deliverable_name', ''), row.get('final_issue_date', '')] for row in eddr],
        )
    else:
        _pptx_add_bullet_slide(prs, 'Engineering Document Deliverable Register', ['EDDR not yet generated.'])

    # 8) Manhours Summary
    by_discipline = manhours.get('by_discipline') or []
    if by_discipline:
        grand_total_hours = manhours.get('grand_total_man_hours')
        grand_total_days = sum(r.get('man_days') or 0 for r in by_discipline)
        footnote = (
            f'Grand Total: {grand_total_days} man-days / {grand_total_hours} man-hours'
            if grand_total_hours is not None else None
        )
        _pptx_add_table_slide(
            prs, 'Manhour Estimate',
            ['Discipline', 'Role', 'Man-Days', 'Man-Hours'],
            [[r.get('discipline_name', ''), r.get('responsible_role', ''), r.get('man_days', ''), r.get('man_hours', '')] for r in by_discipline],
            max_rows=8, footnote=footnote,
        )
    else:
        _pptx_add_bullet_slide(prs, 'Manhour Estimate', ['Manhour estimate not yet generated.'])

    # 9) Validation Summary
    counts = {}
    for issue in validation:
        counts[issue.get('severity', 'pass')] = counts.get(issue.get('severity', 'pass'), 0) + 1
    validation_bullets = [
        f'Pass: {counts.get("pass", 0)}',
        f'Warnings: {counts.get("warning", 0)}',
        f'Critical Issues: {counts.get("critical", 0)}',
    ]
    critical_msgs = [i.get('message', '') for i in validation if i.get('severity') == 'critical']
    if critical_msgs:
        validation_bullets.append('Key issues:')
        validation_bullets.extend(critical_msgs[:5])
    _pptx_add_bullet_slide(prs, 'Validation & Quality Checks', validation_bullets, max_items=10)

    # 10) Narrative / Executive Summary
    narrative_text = (generation.narrative or '').strip()
    paragraphs = [p.strip() for p in narrative_text.split('\n') if p.strip()]
    summary_bullets = paragraphs[:6] if paragraphs else ['Narrative not yet generated.']
    _pptx_add_bullet_slide(prs, 'Executive Summary', summary_bullets, max_items=6)

    # 11) Closing
    _pptx_add_closing_slide(prs, 'Thank You')

    stream = io.BytesIO()
    prs.save(stream)
    return stream.getvalue()


# ─────────────────────────────────────────────────────────────────────────
# Primavera P6 .xer export — schedule-only subset (CURRTYPE / PROJECT /
# CALENDAR / PROJWBS / TASK / TASKPRED). Field layout is soft-coded against
# the P6 XER schema used by the reference sample files supplied for this
# feature (Documents/Project Control/Planning Package/*.xer) — every %F/%R
# column list below mirrors that schema so the exported file opens cleanly
# in Primavera P6 (File > Import > Primavera PM - (XER)). The ERMHDR version
# string itself is soft-coded in ../config.py (PRIMAVERA_XER_VERSION) since
# it must match the target site's installed P6 version, not the schema layout.
# ─────────────────────────────────────────────────────────────────────────
XER_VERSION = PRIMAVERA_XER_VERSION
XER_PROJ_ID = 1
XER_CLNDR_ID = 1
XER_LINE_SEP = '\r\n'  # native XER line ending

# Primavera P6 enforces hard character limits on several short-identifier
# fields at the database schema level. Exceeding them doesn't just truncate
# on import — P6's XER importer silently REJECTS the offending row (and,
# critically, when it's the PROJECT row itself, the entire project — WBS,
# activities, everything — never gets created, so the import appears to
# do nothing at all). Every value written into these fields below is
# capped against these soft-coded limits so that can never happen again.
XER_PROJ_SHORT_NAME_MAX_LEN = 20   # PROJECT.proj_short_name ("Project ID")
XER_WBS_SHORT_NAME_MAX_LEN = 20    # PROJWBS.wbs_short_name ("WBS Code")
XER_WBS_NAME_MAX_LEN = 100         # PROJWBS.wbs_name
XER_TASK_CODE_MAX_LEN = 20         # TASK.task_code ("Activity ID")
XER_TASK_NAME_MAX_LEN = 120        # TASK.task_name ("Activity Name")

_XER_PRED_TYPE = {'FS': 'PR_FS', 'SS': 'PR_SS', 'FF': 'PR_FF', 'SF': 'PR_SF'}


def _derive_proj_short_name(project) -> str:
    """P6's Project ID is a short *code*, not the descriptive project name
    (the reference sample files use e.g. 'Q-101685') — and it's capped at
    `XER_PROJ_SHORT_NAME_MAX_LEN` characters. Prefer a trailing bracketed
    reference number if the project name has one (e.g. '... (30201-50198)'
    -> '30201-50198'), otherwise sanitize the name into a code-safe token.
    """
    name = (project.name or '').strip()
    match = re.search(r'\(([A-Za-z0-9][A-Za-z0-9\-_/]{2,})\)\s*$', name)
    candidate = match.group(1) if match else name
    # P6 Project IDs are conventionally alphanumeric + hyphen/underscore —
    # collapse anything else (spaces, punctuation) into a single hyphen.
    candidate = re.sub(r'[^A-Za-z0-9\-_]+', '-', candidate).strip('-')
    candidate = re.sub(r'-{2,}', '-', candidate)
    return (candidate or 'PROJECT')[:XER_PROJ_SHORT_NAME_MAX_LEN]


def _xer_field(value) -> str:
    """Sanitizes a single tab-delimited field value (XER is TSV-based, one
    record per line — tabs/newlines inside a value would corrupt the file)."""
    if value is None:
        return ''
    return str(value).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()


def _xer_row(*values) -> str:
    return '\t'.join(['%R'] + [_xer_field(v) for v in values])


def _xer_datetime(date_str, time_str='08:00') -> str:
    if not date_str:
        return ''
    return f'{date_str} {time_str}'


def _xer_calendar_data(hours_per_day: int) -> str:
    """Builds a Mon–Fri working calendar (Sat/Sun off) at the given daily
    hour span, in P6's proprietary calendar syntax — mirrors the 'Standard
    5 Day Workweek' calendar found in the reference sample .xer files."""
    start_hr = '08:00'
    end_hr = f'{8 + hours_per_day:02d}:00' if hours_per_day < 16 else '17:00'
    workday = f'(0||0(s|{start_hr}|f|{end_hr})())'
    days = ''.join(
        f'(0||{d}()({workday if 2 <= d <= 6 else ""}))'
        for d in range(1, 8)
    )
    return (
        f'(0||CalendarData()(  (0||DaysOfWeek()(    {days}))  '
        f'(0||VIEW(ShowTotal|Y)())  (0||Exceptions()())))'
    )


def generation_to_xer_bytes(generation) -> bytes:
    """
    Exports this generation's WBS + Activities + Logic Matrix as a
    Primavera P6-compatible .xer schedule file (single project, single
    Mon–Fri calendar). Returns bytes encoded as Windows-1252 (P6's native
    XER encoding) with UTF-8 as a defensive fallback for characters that
    don't map (e.g. non-Latin project names).
    """
    project = generation.project
    wbs_nodes = generation.wbs or []
    activities = generation.activities or []
    logic_matrix = generation.logic_matrix or []

    calendar = dict(DEFAULT_CALENDAR)
    calendar.update(getattr(project, 'calendar_overrides', None) or {})
    hours_per_day = int(calendar.get('hours_per_day') or 8)

    now = timezone.now()
    export_date = now.strftime('%Y-%m-%d')
    export_user = (getattr(generation.generated_by, 'username', '') or 'radai').strip() or 'radai'
    export_user_full = getattr(generation.generated_by, 'get_full_name', lambda: '')() or export_user

    # WBS code -> synthetic numeric wbs_id (P6 requires numeric FKs).
    wbs_id_map = {node['code']: 1000 + i for i, node in enumerate(wbs_nodes, start=1)}
    root_wbs_id = wbs_id_map.get('1') or (next(iter(wbs_id_map.values())) if wbs_id_map else 1000)

    # Activity id (e.g. "PR-100") -> synthetic numeric task_id.
    task_id_map = {a['id']: 2000 + i for i, a in enumerate(activities, start=1)}

    starts = [a['start_date'] for a in activities if a.get('start_date')]
    finishes = [a['finish_date'] for a in activities if a.get('finish_date')]
    plan_start = min(starts) if starts else (project.effective_date.isoformat() if project.effective_date else export_date)
    plan_end = max(finishes) if finishes else plan_start

    lines = [
        f'ERMHDR\t{XER_VERSION}\t{export_date}\tProject\t{export_user}\t{export_user_full}\t'
        f'dbxDatabaseNoName\tProject Management\tUSD'
    ]

    # ── CURRTYPE ──
    lines.append('%T\tCURRTYPE')
    lines.append('%F\tcurr_id\tdecimal_digit_cnt\tcurr_symbol\tdecimal_symbol\tdigit_group_symbol\t'
                  'pos_curr_fmt_type\tneg_curr_fmt_type\tcurr_type\tcurr_short_name\tgroup_digit_cnt\tbase_exch_rate')
    lines.append(_xer_row(1, 2, '$', '.', ',', '#1.1', '(#1.1)', 'US Dollar', 'USD', 3, 1))

    # ── PROJECT ──
    lines.append('%T\tPROJECT')
    lines.append('%F\tproj_id\tfy_start_month_num\trsrc_self_add_flag\tallow_complete_flag\t'
                  'rsrc_multi_assign_flag\tcheckout_flag\tproject_flag\tstep_complete_flag\t'
                  'cost_qty_recalc_flag\tbatch_sum_flag\tname_sep_char\tdef_complete_pct_type\t'
                  'proj_short_name\tclndr_id\ttask_code_base\ttask_code_step\tpriority_num\t'
                  'wbs_max_sum_level\tdef_duration_type\tdef_qty_type\tdef_rate_type\t'
                  'add_act_remain_flag\tact_this_per_link_flag\tdef_task_type\tact_pct_link_flag\t'
                  'critical_drtn_hr_cnt\tplan_start_date\tplan_end_date\tadd_date\t'
                  'last_recalc_date\tscd_end_date\tguid')
    lines.append(_xer_row(
        XER_PROJ_ID, 1, 'N', 'Y', 'Y', 'N', 'Y', 'N', 'N', 'Y', '.', 'CP_Drtn',
        _derive_proj_short_name(project), XER_CLNDR_ID, 1000, 10, 500, 4,
        'DT_FixedDUR2', 'QT_Hour', 'COST_PER_QTY', 'Y', 'Y', 'TT_Task', 'N',
        hours_per_day * 5,
        _xer_datetime(plan_start, '00:00'), _xer_datetime(plan_end, '00:00'),
        now.strftime('%Y-%m-%d %H:%M'), now.strftime('%Y-%m-%d %H:%M'),
        _xer_datetime(plan_end, '00:00'), '',
    ))

    # ── CALENDAR (single Mon-Fri project calendar) ──
    lines.append('%T\tCALENDAR')
    lines.append('%F\tclndr_id\tdefault_flag\tclndr_name\tproj_id\tbase_clndr_id\tlast_chng_date\t'
                  'clndr_type\tday_hr_cnt\tweek_hr_cnt\tmonth_hr_cnt\tyear_hr_cnt\trsrc_private\tclndr_data')
    lines.append(_xer_row(
        # default_flag='N' — this calendar must NOT claim to be the P6
        # database's global default; it only needs to be assigned (via
        # clndr_id) to this project's own WBS/activities.
        XER_CLNDR_ID, 'N', 'Standard 5 Day Workweek', '', '', now.strftime('%Y-%m-%d %H:%M'),
        'CA_Base', hours_per_day, hours_per_day * 5, hours_per_day * 5 * 4, hours_per_day * 5 * 52,
        'N', _xer_calendar_data(hours_per_day),
    ))

    # ── PROJWBS ──
    lines.append('%T\tPROJWBS')
    lines.append('%F\twbs_id\tproj_id\tobs_id\tseq_num\tproj_node_flag\tsum_data_flag\t'
                  'status_code\twbs_short_name\twbs_name\tparent_wbs_id')
    for i, node in enumerate(wbs_nodes, start=1):
        parent_code = node.get('parent_code')
        lines.append(_xer_row(
            wbs_id_map[node['code']], XER_PROJ_ID, '', i,
            'Y' if node.get('level') == 0 else 'N', 'N', 'WS_Open',
            node['code'].split('.')[-1][:XER_WBS_SHORT_NAME_MAX_LEN],
            (node.get('name') or '')[:XER_WBS_NAME_MAX_LEN],
            wbs_id_map.get(parent_code, '') if parent_code else '',
        ))

    # ── TASK ──
    lines.append('%T\tTASK')
    lines.append('%F\ttask_id\tproj_id\twbs_id\tclndr_id\tphys_complete_pct\ttask_type\t'
                  'duration_type\tstatus_code\ttask_code\ttask_name\ttotal_float_hr_cnt\t'
                  'free_float_hr_cnt\tremain_drtn_hr_cnt\ttarget_drtn_hr_cnt\tact_start_date\t'
                  'act_end_date\tlate_start_date\tlate_end_date\tearly_start_date\tearly_end_date\t'
                  'target_start_date\ttarget_end_date\tguid')
    for activity in activities:
        wbs_id = wbs_id_map.get(activity.get('wbs_code'), root_wbs_id)
        duration_hrs = int((activity.get('original_duration_days') or 0) * hours_per_day)
        float_days = activity.get('total_float_days') or 0
        float_hrs = int(float_days * hours_per_day)

        start_raw = activity.get('start_date')
        finish_raw = activity.get('finish_date')
        early_start = _xer_datetime(start_raw)
        early_end = _xer_datetime(finish_raw, '17:00' if not activity.get('is_milestone') else '08:00')

        late_start, late_end = early_start, early_end
        if float_days and start_raw and finish_raw:
            try:
                late_start_date = add_working_days(datetime.date.fromisoformat(start_raw), int(float_days))
                late_end_date = add_working_days(datetime.date.fromisoformat(finish_raw), int(float_days))
                late_start = _xer_datetime(late_start_date.isoformat())
                late_end = _xer_datetime(late_end_date.isoformat(), '17:00')
            except (ValueError, TypeError):
                pass

        lines.append(_xer_row(
            task_id_map[activity['id']], XER_PROJ_ID, wbs_id, XER_CLNDR_ID, 0,
            'TT_Mile' if activity.get('is_milestone') else 'TT_Task', 'DT_FixedDUR2',
            'TK_NotStart', str(activity['id'])[:XER_TASK_CODE_MAX_LEN],
            (activity.get('name') or '')[:XER_TASK_NAME_MAX_LEN],
            float_hrs, float_hrs, duration_hrs, duration_hrs,
            '', '', late_start, late_end, early_start, early_end, early_start, early_end, '',
        ))

    # ── TASKPRED ──
    lines.append('%T\tTASKPRED')
    lines.append('%F\ttask_pred_id\ttask_id\tpred_task_id\tproj_id\tpred_proj_id\tpred_type\tlag_hr_cnt')
    pred_seq = 1
    for link in logic_matrix or []:
        task_id = task_id_map.get(link.get('activity_id') or link.get('task_id'))
        pred_id = task_id_map.get(link.get('predecessor_id') or link.get('pred_task_id'))
        if not task_id or not pred_id:
            continue
        pred_type = _XER_PRED_TYPE.get((link.get('type') or 'FS').upper(), 'PR_FS')
        lines.append(_xer_row(pred_seq, task_id, pred_id, XER_PROJ_ID, XER_PROJ_ID, pred_type, 0))
        pred_seq += 1
    if not (logic_matrix and pred_seq > 1):
        # Fall back to each activity's own `predecessors` list (always
        # present, even if the separate logic_matrix export is empty).
        for activity in activities:
            task_id = task_id_map.get(activity['id'])
            for pred in activity.get('predecessors') or []:
                pred_id = task_id_map.get(pred.get('id'))
                if not task_id or not pred_id:
                    continue
                pred_type = _XER_PRED_TYPE.get((pred.get('type') or 'FS').upper(), 'PR_FS')
                lines.append(_xer_row(pred_seq, task_id, pred_id, XER_PROJ_ID, XER_PROJ_ID, pred_type, 0))
                pred_seq += 1

    lines.append('%E')

    text = XER_LINE_SEP.join(lines) + XER_LINE_SEP
    try:
        return text.encode('cp1252')
    except UnicodeEncodeError:
        return text.encode('utf-8')
