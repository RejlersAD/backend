"""
Export Service (MODULE 12) — CSV / JSON / Primavera-ready CSV / Excel / PowerPoint.

CSV & JSON are the MVP-required minimum bar per spec. Excel, a
Primavera-compatible column layout, and a client-ready PowerPoint deck are
provided as they only need libraries already in requirements.txt (openpyxl,
python-pptx) — no extra dependency risk beyond python-pptx itself.
"""
from __future__ import annotations

import csv
import io
import json
import os

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
