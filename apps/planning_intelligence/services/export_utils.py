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

# Brand palette for the PPTX deck — mirrors the RADAI frontend's
# violet/indigo hero gradient (see PLANNING_UI.heroGradient in
# frontend/src/config/planningIntelligence.config.js) so the exported deck
# feels consistent with the web app.
PPTX_BRAND_PRIMARY_RGB = (109, 40, 217)     # violet-700
PPTX_BRAND_SECONDARY_RGB = (67, 56, 202)    # indigo-700
PPTX_BRAND_ACCENT_RGB = (15, 23, 42)        # slate-900
PPTX_BRAND_LIGHT_RGB = (243, 244, 246)      # slate-100
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


def _pptx_add_title_slide(prs, project, generation):
    from pptx.util import Pt

    slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank layout
    _pptx_fill_background(slide, PPTX_BRAND_PRIMARY_RGB)

    title_box = slide.shapes.add_textbox(prs.slide_width * 0.08, prs.slide_height * 0.32,
                                          prs.slide_width * 0.84, prs.slide_height * 0.22)
    tf = title_box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = project.name or 'Untitled Planning Project'
    run.font.size = Pt(40)
    run.font.bold = True
    run.font.color.rgb = _pptx_rgb((255, 255, 255))

    subtitle_bits = [b for b in [project.phase, project.client, project.location] if b]
    subtitle_box = slide.shapes.add_textbox(prs.slide_width * 0.08, prs.slide_height * 0.54,
                                             prs.slide_width * 0.84, prs.slide_height * 0.12)
    stf = subtitle_box.text_frame
    stf.word_wrap = True
    sp = stf.paragraphs[0]
    srun = sp.add_run()
    srun.text = ' · '.join(subtitle_bits) if subtitle_bits else 'Project Planning Presentation'
    srun.font.size = Pt(18)
    srun.font.color.rgb = _pptx_rgb((237, 233, 254))  # violet-100

    footer_box = slide.shapes.add_textbox(prs.slide_width * 0.08, prs.slide_height * 0.86,
                                           prs.slide_width * 0.84, prs.slide_height * 0.08)
    ftf = footer_box.text_frame
    fp = ftf.paragraphs[0]
    frun = fp.add_run()
    frun.text = f'RADAI Project Planning Application · Schedule v{generation.version}'
    frun.font.size = Pt(12)
    frun.font.color.rgb = _pptx_rgb((196, 181, 253))  # violet-200


def _pptx_rgb(rgb_tuple):
    from pptx.dml.color import RGBColor
    return RGBColor(*rgb_tuple)


def _pptx_fill_background(slide, rgb_tuple):
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _pptx_rgb(rgb_tuple)


def _pptx_add_content_slide(prs, heading, icon=''):
    """Adds a blank slide with a colored heading bar; returns (slide, content_top_emu)."""
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.util import Pt, Emu

    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _pptx_fill_background(slide, (255, 255, 255))

    bar_height = Emu(int(prs.slide_height * 0.14))
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, 0, 0, prs.slide_width, bar_height)
    bar.fill.solid()
    bar.fill.fore_color.rgb = _pptx_rgb(PPTX_BRAND_SECONDARY_RGB)
    bar.line.fill.background()
    bar.shadow.inherit = False

    tf = bar.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = f'{icon}  {heading}'.strip()
    run.font.size = Pt(26)
    run.font.bold = True
    run.font.color.rgb = _pptx_rgb((255, 255, 255))

    return slide, int(bar_height) + Emu(int(prs.slide_height * 0.04))


def _pptx_add_bullets(slide, prs, top_emu, bullets, max_items=10):
    from pptx.util import Pt, Emu

    box = slide.shapes.add_textbox(Emu(int(prs.slide_width * 0.08)), Emu(top_emu),
                                    Emu(int(prs.slide_width * 0.84)), Emu(int(prs.slide_height * 0.75)))
    tf = box.text_frame
    tf.word_wrap = True
    items = bullets[:max_items] or ['No data available.']
    for i, text in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        run = p.add_run()
        run.text = f'•  {text}'
        run.font.size = Pt(16)
        run.font.color.rgb = _pptx_rgb((30, 41, 59))  # slate-800
        p.space_after = Pt(8)
    if len(bullets) > max_items:
        p = tf.add_paragraph()
        run = p.add_run()
        run.text = f'…and {len(bullets) - max_items} more (see full export for details).'
        run.font.size = Pt(13)
        run.font.italic = True
        run.font.color.rgb = _pptx_rgb((100, 116, 139))  # slate-500


def _pptx_add_table(slide, prs, top_emu, headers, rows, max_rows=PPTX_MAX_TABLE_ROWS):
    from pptx.util import Pt, Emu

    display_rows = rows[:max_rows]
    n_rows = len(display_rows) + 1
    n_cols = len(headers)
    table_height = Emu(int(prs.slide_height * min(0.6, 0.06 * n_rows)))
    table_shape = slide.shapes.add_table(
        n_rows, n_cols,
        Emu(int(prs.slide_width * 0.06)), Emu(top_emu),
        Emu(int(prs.slide_width * 0.88)), table_height,
    )
    table = table_shape.table

    for c, header in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = str(header)
        for run in cell.text_frame.paragraphs[0].runs:
            run.font.bold = True
            run.font.size = Pt(12)
            run.font.color.rgb = _pptx_rgb((255, 255, 255))
        cell.fill.solid()
        cell.fill.fore_color.rgb = _pptx_rgb(PPTX_BRAND_ACCENT_RGB)

    for r, row in enumerate(display_rows, start=1):
        for c, value in enumerate(row):
            cell = table.cell(r, c)
            cell.text = '' if value is None else str(value)
            for run in cell.text_frame.paragraphs[0].runs:
                run.font.size = Pt(11)
                run.font.color.rgb = _pptx_rgb((30, 41, 59))
            cell.fill.solid()
            cell.fill.fore_color.rgb = _pptx_rgb(PPTX_BRAND_LIGHT_RGB if r % 2 == 0 else (255, 255, 255))

    if len(rows) > max_rows:
        note_top = Emu(int(top_emu) + int(table_height) + Emu(int(prs.slide_height * 0.02)))
        note = slide.shapes.add_textbox(Emu(int(prs.slide_width * 0.06)), note_top,
                                         Emu(int(prs.slide_width * 0.88)), Emu(int(prs.slide_height * 0.06)))
        p = note.text_frame.paragraphs[0]
        run = p.add_run()
        run.text = f'Showing {max_rows} of {len(rows)} rows — full detail available via CSV/Excel export.'
        run.font.size = Pt(11)
        run.font.italic = True
        run.font.color.rgb = _pptx_rgb((100, 116, 139))


def generation_to_pptx_bytes(generation) -> bytes:
    """
    Builds a client/internal-review-ready PowerPoint deck summarizing a
    PlanningGeneration: title, overview, WBS, schedule, EDDR, manhours,
    validation and narrative/executive-summary slides.
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt

    project = generation.project
    activities = generation.activities or []
    wbs = generation.wbs or []
    eddr = generation.eddr or []
    manhours = generation.manhours or {}
    validation = generation.validation or []
    intelligence = generation.intelligence or {}

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    # 1) Title
    _pptx_add_title_slide(prs, project, generation)

    # 2) Project Overview
    slide, top = _pptx_add_content_slide(prs, 'Project Overview', '🧭')
    overview_bullets = [
        f'Client: {project.client or "—"}',
        f'Location: {project.location or "—"}',
        f'Phase: {project.phase or "—"}',
        f'Effective Date: {project.effective_date or "—"}',
        f'Planned Duration: {project.duration_months} month(s)',
        f'Reference Documents Analyzed: {len(intelligence.get("disciplines", {}) or {})} discipline(s)',
        f'Schedule Version: v{generation.version}',
    ]
    _pptx_add_bullets(slide, prs, top, overview_bullets)

    # 3) WBS Summary
    slide, top = _pptx_add_content_slide(prs, 'Work Breakdown Structure', '🗂️')
    top_level = [n for n in wbs if n.get('level') == 1] or wbs
    wbs_bullets = [f'{n.get("code", "")} — {n.get("name", "")}' for n in top_level]
    if not wbs_bullets:
        wbs_bullets = ['WBS not yet generated.']
    else:
        wbs_bullets.insert(0, f'{len(wbs)} total WBS node(s) across the project.')
    _pptx_add_bullets(slide, prs, top, wbs_bullets)

    # 4) Schedule Summary
    slide, top = _pptx_add_content_slide(prs, 'Schedule Summary', '📅')
    critical = [a for a in activities if a.get('is_critical')]
    milestones = generation.milestones or [a for a in activities if a.get('is_milestone')]
    finish_dates = [a.get('finish_date') for a in activities if a.get('finish_date')]
    schedule_bullets = [
        f'Total Activities: {len(activities)}',
        f'Critical Path Activities: {len(critical)}',
        f'Milestones: {len(milestones)}',
        f'Projected Finish: {max(finish_dates) if finish_dates else "—"}',
    ]
    _pptx_add_bullets(slide, prs, top, schedule_bullets, max_items=6)
    if milestones:
        table_top = top + Inches(2.0)
        _pptx_add_table(
            slide, prs, table_top,
            ['Milestone', 'Finish Date'],
            [[m.get('name', ''), m.get('finish_date', '')] for m in milestones],
            max_rows=8,
        )

    # 5) EDDR Summary
    slide, top = _pptx_add_content_slide(prs, 'Engineering Document Deliverable Register', '📋')
    if eddr:
        _pptx_add_table(
            slide, prs, top,
            ['Discipline', 'Deliverable', 'Final Issue'],
            [[row.get('discipline', ''), row.get('deliverable_name', ''), row.get('final_issue_date', '')] for row in eddr],
        )
    else:
        _pptx_add_bullets(slide, prs, top, ['EDDR not yet generated.'])

    # 6) Manhours Summary
    slide, top = _pptx_add_content_slide(prs, 'Manhour Estimate', '⏱️')
    by_discipline = manhours.get('by_discipline') or []
    if by_discipline:
        _pptx_add_table(
            slide, prs, top,
            ['Discipline', 'Role', 'Man-Days', 'Man-Hours'],
            [[r.get('discipline_name', ''), r.get('responsible_role', ''), r.get('man_days', ''), r.get('man_hours', '')] for r in by_discipline],
            max_rows=8,
        )
        note_top = top + Inches(min(5.5, 0.5 * (len(by_discipline[:8]) + 1) + 0.3))
        note = slide.shapes.add_textbox(Inches(0.8), note_top, Inches(11.5), Inches(0.6))
        p = note.text_frame.paragraphs[0]
        run = p.add_run()
        run.text = f'Grand Total: {manhours.get("grand_total_man_hours", "—")} man-hours'
        run.font.bold = True
        run.font.size = Pt(15)
    else:
        _pptx_add_bullets(slide, prs, top, ['Manhour estimate not yet generated.'])

    # 7) Validation Summary
    slide, top = _pptx_add_content_slide(prs, 'Validation & Quality Checks', '✅')
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
    _pptx_add_bullets(slide, prs, top, validation_bullets, max_items=10)

    # 8) Narrative / Executive Summary
    slide, top = _pptx_add_content_slide(prs, 'Executive Summary', '📝')
    narrative_text = (generation.narrative or '').strip()
    paragraphs = [p.strip() for p in narrative_text.split('\n') if p.strip()]
    summary_bullets = paragraphs[:6] if paragraphs else ['Narrative not yet generated.']
    _pptx_add_bullets(slide, prs, top, summary_bullets, max_items=6)

    stream = io.BytesIO()
    prs.save(stream)
    return stream.getvalue()
