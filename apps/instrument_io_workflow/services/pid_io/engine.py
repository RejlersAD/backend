"""Extraction orchestrator — runs §6.1–6.8 and returns one evidence-carrying
result dict matching the §8 data contract (PidTag / LineLabel / Untagged /
VendorFill shapes)."""
import time

from . import baseline as baseline_mod
from . import (alarmtrip, connections, ioclass, pipes, service, setpoints,
               spans, tags, vendor as vendor_mod)


def run_extraction(pid_path, baseline_path=None, vendor_paths=(), legend_rules=None,
                   progress=None):
    """Deterministic drawing-rules extraction. `pid_path` may be one PDF path
    or a list of them (every uploaded P&ID is processed; sheets are numbered
    continuously across files). `progress(step, detail)` is called between
    steps for the job tracker. Never raises on drawing quirks — problems land
    in the row's evidence/warnings instead."""
    t0 = time.monotonic()
    warnings = []

    def mark(step, detail=''):
        if progress:
            progress(step, detail)

    # ── §6.1–6.4 per sheet (all uploaded P&ID files) ───────────────────
    mark('reading', 'Reading text and vector layers')
    pid_paths = [pid_path] if isinstance(pid_path, str) else list(pid_path)
    pages = []
    for path in pid_paths:
        file_pages = spans.read_document(path)
        for pg in file_pages:
            pg.page_no = len(pages)      # continuous sheet numbering
            pages.append(pg)
    all_tags, all_untagged, all_labels = [], [], []
    all_cause_notes = []
    conns_by_page, pages_by_no, eq_by_page = {}, {}, {}
    scales_by_page, psv_by_page = {}, {}

    sheets = {}
    for page in pages:
        mark('tags', f'Sheet {page.page_no + 1}: tags and bubbles')
        t = tags.pair_tags(page, legend_rules=legend_rules)
        t = tags.attach_bubbles(page, t)
        t = tags.attach_system_letters(page, t)
        t = tags.attach_alarms(page, t)
        tags.confirm_with_smart_tags(page, t)
        all_tags += t
        all_untagged += tags.classify_untagged(page, t)
        all_cause_notes += spans.extract_cause_notes(page)

        mark('pipes', f'Sheet {page.page_no + 1}: pipes, lines, equipment')
        eq = pipes.find_equipment(page)
        psegs, leaders = pipes.pipe_segments(page)
        runs = pipes.build_runs(psegs, page.page_no)
        labels = pipes.anchor_labels(pipes.find_line_labels(page), runs, leaders, page)
        all_labels += labels

        cls = ioclass.classify_all(t)
        for c in cls:
            c.tag._contact = connections.find_contact(page, c.tag, runs, eq, all_tags=t)
        cls = connections.assign_lines(cls, labels, runs)

        conns = service.find_connectors(page)
        for cn in conns:
            cn.direction = service._connector_direction(page, cn)
        conns_by_page[page.page_no] = conns
        pages_by_no[page.page_no] = page
        eq_by_page[page.page_no] = eq
        scales_by_page[page.page_no] = setpoints.read_vessel_scales(page, eq)
        psv_by_page[page.page_no] = setpoints.find_psv_pressures(page)
        sheets[page.page_no] = {'classified': cls, 'labels': labels, 'runs': runs}

    # de-duplicate: a bubble drawn on two sheets (panel and field) is kept once
    seen = {}
    for c_page in sheets.values():
        for c in c_page['classified']:
            key = c.tag.tag
            if key in seen:
                c.duplicate_of = seen[key]
            else:
                seen[key] = c
    all_classified = list(seen.values())

    # ── §6.5 services ──────────────────────────────────────────────────
    mark('service', 'Generating services from drawing rules')
    loops = {}
    for c in all_classified:
        loops.setdefault(c.tag.loop_key, []).append(c)
    for members in loops.values():
        for m in members:
            res = service.generate_service(
                m, members, conns_by_page, all_loops=loops,
                pages=pages_by_no, equipment_by_page=eq_by_page)
            m.tag.service = res.service
            m.tag.service_confidence = res.confidence
            m.tag.service_evidence = res.evidence
            m.tag.service_alt = res.alternative

    # ── §6.6 set points ────────────────────────────────────────────────
    mark('setpoints', 'Reading set points')
    all_setpoints = setpoints.extract_setpoints(all_classified, scales_by_page, psv_by_page)

    # ── §6.7 derived rows ──────────────────────────────────────────────
    derived = ioclass.derive_valve_typical_rows(all_classified)

    # ── §6.8 vendor auto-fill ──────────────────────────────────────────
    vendor_fills, vendor_conflicts, no_vendor, baseline_info = [], [], [], {}
    if baseline_path:
        mark('baseline', 'Reading IO list baseline')
        baseline_info = baseline_mod.read_baseline(baseline_path)
        if 'error' in baseline_info:
            warnings.append(baseline_info['error'])
            baseline_info = {}
    if vendor_paths:
        mark('vendor', 'Matching vendor IO databases')
        dbs = []
        for path in vendor_paths:
            db = vendor_mod.read_vendor_db(path)
            if isinstance(db, dict) and 'error' in db:
                warnings.append(db['error'])
            else:
                dbs.append(db)
        if baseline_info:
            fills, vendor_conflicts, no_vendor = vendor_mod.autofill(
                baseline_info['rows'], dbs)
            vendor_fills = fills
        elif dbs:
            warnings.append(
                'Vendor data matched without an IO-list baseline — upload the '
                'current IO list to enable comparison (conflicts, coverage).')
        # vendor data must reach the extracted rows even without a baseline:
        # match the extracted field-I/O tags directly against the vendor DBs
        io_norms = [c.tag.tag.replace(' ', '') for c in all_classified
                    if c.is_field_io]
        io_norms += [d.get('norm') for d in derived if d.get('norm')]
        direct_fills, direct_no_record = vendor_mod.match_tags(io_norms, dbs)
        for tag, f in direct_fills.items():
            if tag in vendor_fills:
                # baseline-driven cells win; direct adds the remaining vendor cells
                merged = {**f['cells'], **vendor_fills[tag]['cells']}
                vendor_fills[tag] = {'cells': merged,
                                     'source': vendor_fills[tag].get('source') or f['source']}
            else:
                vendor_fills[tag] = f
        no_vendor = sorted(set(no_vendor) | set(direct_no_record))

    # ── assemble result (§8 contract) ──────────────────────────────────
    mark('assemble', 'Building the register')
    # drawing number per sheet — detected from the title block, matched
    # against the baseline's P&ID vocabulary when one exists (works with any
    # client numbering scheme); drives the coverage report
    from . import docrefs
    baseline_drawings = sorted({
        docrefs.norm_drawing(r.get('P&ID'))
        for r in (baseline_info.get('rows') if baseline_info else [])
        if docrefs.norm_drawing(r.get('P&ID'))})
    sheet_drawings = {pg.page_no: docrefs.detect_sheet_drawing(pg, baseline_drawings)
                      for pg in pages}
    pid_tags = [_tag_payload(c) for c in all_classified]
    for d in derived:
        pid_tags.append({
            'tag': d['tag'], 'norm': d['tag'], 'page': d['page'], 'x': d['x'], 'y': d['y'],
            'unit': d['unit'], 'func': d['func'], 'loop': d['loop'], 'loopKey': d['loop_key'],
            'system': d['system'], 'bubble': 'derived', 'ioClass': d['io_class'],
            'ioType': d['io_type'], 'alarms': [], 'confirmedBySmartTag': False,
            'contact': '', 'equipment': '', 'line': '', 'lineSource': '', 'lineAlt': '',
            'service': '', 'serviceConfidence': 0.0, 'serviceEvidence': d['reason'],
            'serviceAlt': '', 'derived': True,
        })
    line_rows = [{
        'sheet': lb.page + 1, 'line': lb.line, 'size': lb.size,
        'serviceCode': lb.service_code, 'pipeClass': lb.pipe_class,
        'anchoredBy': lb.anchored_by,
        'tagsOwn': sorted({c.tag.tag for c in all_classified
                           if c.tag.line == lb.line and c.tag.line_source.startswith(('own', 'via'))}),
        'tagsLoop': sorted({c.tag.tag for c in all_classified
                            if c.tag.line == lb.line and c.tag.line_source.startswith('inherited')}),
    } for lb in all_labels]

    ai_candidates = [p for p in pid_tags
                     if not p.get('derived') and p.get('serviceConfidence', 0) < 0.6
                     and p.get('ioClass') in ('AI', 'AO', 'DI', 'DO', 'soft')]
    ai_loops = sorted({p['loopKey'] for p in ai_candidates})

    vendor_payload = {
        'fills': vendor_fills,
        'conflicts': vendor_conflicts,
        'noRecord': no_vendor,
        'baseline': {k: v for k, v in baseline_info.items() if k != 'rows'} if baseline_info else {},
        # full baseline row set (stringified) so exports rebuild the
        # 53-column layout without re-reading the workbook
        'baselineRows': [
            {k: ('' if v is None else str(v)) for k, v in row.items()}
            for row in (baseline_info.get('rows') if baseline_info else [])
        ],
    }

    # ── P&ID coverage vs baseline ──────────────────────────────────────
    # Which baseline rows belong to the uploaded drawings, and how many of
    # those tags the extraction actually found. Baseline rows on drawings
    # that were never uploaded are listed so the reviewer knows the gap is
    # missing INPUT, not an extractor miss.
    coverage = {}
    if baseline_info:
        found_norms = {p['norm'].upper() for p in pid_tags}
        uploaded_drawings = sorted({d for d in sheet_drawings.values() if d})

        def _drawing_key(pid_ref):
            return docrefs.norm_drawing(pid_ref)

        on_uploaded, not_uploaded = {}, {}
        for row in baseline_info.get('rows', []):
            dk = _drawing_key(row.get('P&ID'))
            tag = row.get('_tag_norm', '')
            if dk in uploaded_drawings:
                e = on_uploaded.setdefault(dk, {'rows': 0, 'found': 0, 'missing': []})
                e['rows'] += 1
                if tag.upper() in found_norms:
                    e['found'] += 1
                else:
                    e['missing'].append(row.get('TAG_Number'))
            elif dk:
                not_uploaded[dk] = not_uploaded.get(dk, 0) + 1
        coverage = {
            'uploadedDrawings': uploaded_drawings,
            'onUploadedDrawings': [
                {'drawing': k, 'baselineRows': v['rows'], 'found': v['found'],
                 'missing': sorted(v['missing'])}
                for k, v in sorted(on_uploaded.items())],
            'notUploaded': [{'drawing': k, 'baselineRows': v}
                            for k, v in sorted(not_uploaded.items())],
            'notUploadedRows': sum(not_uploaded.values()),
        }

    # ── Rev 2: cause notes + alarm & trip register ─────────────────────
    mark('alarmtrip', 'Building the alarm & trip register')
    setpoint_rows = [{'tag': s.tag, 'point': s.point, 'value': s.value, 'unit': s.unit,
                      'label': s.label, 'column': s.column, 'sheet': s.page + 1,
                      'evidence': s.evidence} for s in all_setpoints]
    alarmtrip_payload = {
        scope: alarmtrip.build(pid_tags, setpoint_rows,
                               vendor_payload['baselineRows'], all_cause_notes,
                               scope=scope)
        for scope in ('pid', 'all')
    }

    return {
        'tags': pid_tags,
        'lines': line_rows,
        'untagged': [{'sheet': u['page'] + 1, 'text': u['text'], 'kind': u['kind'],
                      'nearestTag': u['nearest_tag'], 'distancePt': u['distance_pt']}
                     for u in all_untagged],
        'setpoints': setpoint_rows,
        'vendor': vendor_payload,
        'coverage': coverage,
        'cause_notes': all_cause_notes,
        'alarmtrip': alarmtrip_payload,
        'summary': {
            'sheets': len(pages),
            'tags': len(all_classified),
            'confirmed': sum(1 for c in all_classified if c.tag.confirmed_by_smart_tag),
            'fieldIO': sum(1 for c in all_classified if c.is_field_io) + len(derived),
            'derived': len(derived),
            'withLine': sum(1 for c in all_classified if c.tag.line),
            'setpoints': sum(1 for s in all_setpoints if s.value),
            'untagged': len(all_untagged),
            'labels': len(all_labels),
            'aiTags': len(ai_candidates),
            'aiLoops': len(ai_loops),
            'aiEstimate': len(ai_loops),
            'vendorMatched': len(vendor_fills),
            'vendorCells': sum(len(f['cells']) for f in vendor_fills.values()) if vendor_fills else 0,
            'vendorConflicts': sum(1 for c in vendor_conflicts if c['type'] == 'engineering'),
            'causeNotes': len(all_cause_notes),
            'coverage': coverage,
            'coverageFound': sum(d['found'] for d in coverage.get('onUploadedDrawings', [])),
            'coverageRows': sum(d['baselineRows'] for d in coverage.get('onUploadedDrawings', [])),
            'notUploadedRows': coverage.get('notUploadedRows', 0),
            'elapsed_s': round(time.monotonic() - t0, 1),
        },
        'warnings': warnings,
    }


def _tag_payload(c):
    t = c.tag
    return {
        'tag': t.tag, 'norm': t.tag.replace(' ', ''), 'page': t.page,
        'x': round(t.x, 1), 'y': round(t.y, 1),
        'unit': t.unit, 'func': t.func, 'loop': t.loop, 'loopKey': t.loop_key,
        'system': t.system, 'bubble': t.bubble, 'ioClass': c.io_class, 'ioType': c.io_type,
        'alarms': t.alarms, 'confirmedBySmartTag': t.confirmed_by_smart_tag,
        'contact': getattr(t, 'contact', ''), 'equipment': getattr(t, 'equipment', ''),
        'line': t.line, 'lineSource': t.line_source, 'lineAlt': t.line_alt,
        'service': getattr(t, 'service', ''),
        'serviceConfidence': getattr(t, 'service_confidence', 0.0),
        'serviceEvidence': getattr(t, 'service_evidence', ''),
        'serviceAlt': getattr(t, 'service_alt', ''),
        'setpoints': [], 'derived': False,
    }
