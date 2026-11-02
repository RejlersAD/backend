"""
SLD Checker engineering rules
=============================

Every rule is a function ``fn(ctx) -> [finding dict]``. A finding dict has
the SldFinding fields except ``number`` (assigned by the pipeline after
severity sorting).

Conventions (spec §rules):
- every *estimate* is a Query with its assumptions spelled out in `basis`;
- a missing or needs_review input → Not Verifiable naming the value;
- values read below SldCheckConfig.conf_min are skipped by rules
  (``ctx.val()`` returns None for them).

``RULE_NODE_FIELDS`` soft-codes which node fields each rule reads — the
PATCH /nodes/ endpoint re-runs only rules whose fields were touched.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .checklist_map import RULE_TO_ITEMS

SQRT3 = math.sqrt(3)

FIRE_PUMP_RE = re.compile(r'fire pump|sprinkler|jockey', re.I)
SPARE_RE = re.compile(r'\bspare\b|\bspace\b', re.I)


@dataclass
class Ctx:
    analysis_id: str
    config: object                       # SldCheckConfig
    nodes: list                          # saved SldNode rows
    edges: list                          # saved SldEdge rows
    interlock_boards: set = field(default_factory=set)  # norm_tags with interlock text nearby

    # ── helpers ─────────────────────────────────────────────────────────
    def val(self, node, field_name):
        """A value usable by rules: None when missing or below conf_min."""
        if getattr(node, 'needs_review', False):
            return None
        return getattr(node, field_name, None)

    def by_kind(self, *kinds):
        return [n for n in self.nodes if n.kind in kinds]

    def by_norm(self):
        return {n.norm_tag: n for n in self.nodes}

    def incomers(self, board):
        return [e for e in self.edges if e.to_node_id == board.id and e.kind in ('incomer', 'alternative')]

    def feeders(self, board):
        return [e for e in self.edges if e.from_node_id == board.id and e.kind == 'feeder']

    def ties(self, board):
        return [e for e in self.edges if e.kind == 'tie'
                and (e.from_node_id == board.id or e.to_node_id == board.id)]

    def gen_fed_boards(self):
        """Boards with a generator incomer (directly or via a tie)."""
        gens = {n.id for n in self.by_kind('generator')}
        fed = set()
        for e in self.edges:
            if e.kind in ('incomer', 'alternative') and e.from_node_id in gens:
                fed.add(e.to_node_id)
        # propagate across ties (sections of one board share the gen feed)
        changed = True
        while changed:
            changed = False
            for e in self.edges:
                if e.kind != 'tie':
                    continue
                for a, b in ((e.from_node_id, e.to_node_id), (e.to_node_id, e.from_node_id)):
                    if a in fed and b not in fed:
                        fed.add(b)
                        changed = True
        return fed


def _finding(ctx, rule_id, family, severity, result, title, found='', estimate='',
             basis='', action='', nodes=(), sheet=None, bbox=None, confidence=None):
    return {
        'rule_id': rule_id, 'family': family, 'severity': severity, 'result': result,
        'title': title, 'found': found, 'estimate': estimate, 'basis': basis,
        'action': action, 'node_ids': [str(n.id) for n in nodes],
        'sheet': sheet, 'bbox': bbox,
        'checklist_items': RULE_TO_ITEMS.get(rule_id, []),
        'confidence': confidence,
    }


def _tx_isc_ka(tx, voltage_v, ctx):
    """Transformer prospective contribution in kA; None + name when a value
    is missing/needs-review."""
    kva = ctx.val(tx, 'kva')
    z = ctx.val(tx, 'z_pct')
    if kva is None:
        return None, f'{tx.tag} kVA'
    if z is None:
        return None, f'{tx.tag} Z%'
    if not voltage_v:
        return None, f'{tx.tag} secondary voltage'
    return kva / (SQRT3 * voltage_v) / (z / 100.0) / 1000.0, None


def _gen_isc_ka(gen, voltage_v, ctx):
    kva = ctx.val(gen, 'kva')
    xd = ctx.val(gen, 'xd_pct')
    if kva is None:
        return None, f'{gen.tag} kVA'
    if not voltage_v:
        return None, f'{gen.tag} voltage'
    assumed = False
    if xd is None:
        xd = ctx.config.xd_pct_default
        assumed = True
    flc = kva / (SQRT3 * voltage_v)
    return flc / (xd / 100.0) / 1000.0, ('X"d assumed %g%% (not on SLD)' % xd) if assumed else None


def _bus_estimate_ka(ctx, board):
    """(estimate_kA or None, notes, missing) — Σ transformer kVA/(√3·V)/Z
    + generator FLC/X"d over the board's incomers. Infinite source behind
    the transformers; no cable impedance or motor contribution."""
    notes, missing, total = [], [], 0.0
    v = ctx.val(board, 'voltage_v')
    for e in ctx.incomers(board):
        src = next((n for n in ctx.nodes if n.id == e.from_node_id), None)
        if src is None:
            continue
        if src.kind == 'transformer':
            isc, why = _tx_isc_ka(src, v, ctx)
        elif src.kind == 'generator':
            isc, why = _gen_isc_ka(src, v, ctx)
            if why and why.startswith('X"d'):
                notes.append(why)
                why = None
        elif src.kind == 'source':
            isc, why = None, None  # infinite source: bounded by transformer Z
            if not any(n.kind == 'transformer' for n in ctx.nodes):
                isc, why = None, f'{src.tag} upstream impedance'
        else:
            isc, why = None, None
        if isc is None and why:
            missing.append(why)
        elif isc:
            total += isc
    return (total if total > 0 else None), notes, missing


SC_BASIS = ('Estimate: infinite source behind transformer, '
            'Isc = Σ kVA/(√3·V)/Z + Σ generator FLC/X"d; '
            'no cable impedance, no motor contribution.')


# ── ELE-SC-01 board kA vs estimated prospective fault ────────────────────
def rule_sc01(ctx):
    out = []
    for b in ctx.by_kind('board'):
        ka = ctx.val(b, 'isc_ka')
        est, notes, missing = _bus_estimate_ka(ctx, b)
        if ka is None:
            out.append(_finding(ctx, 'ELE-SC-01', 'engineering', 'critical', 'Not Verifiable',
                                f'Board short-circuit rating not readable — {b.tag}',
                                found=f'{b.tag}: kA rating not found on the SLD.',
                                basis=SC_BASIS, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                action='Confirm the board short-time / peak withstand rating.'))
            continue
        if missing or est is None:
            out.append(_finding(ctx, 'ELE-SC-01', 'engineering', 'critical', 'Not Verifiable',
                                f'Prospective fault level not verifiable — {b.tag}',
                                found=f'{b.tag}: board rated {ka:g} kA.',
                                basis=SC_BASIS, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                estimate='Missing input: ' + ', '.join(missing or ['incomer data'])))
            continue
        basis = SC_BASIS + (' ' + '; '.join(notes) if notes else '')
        if est > ka:
            out.append(_finding(ctx, 'ELE-SC-01', 'engineering', 'critical', 'Non-Conformance',
                                f'Estimated fault level exceeds board rating — {b.tag}',
                                found=f'{b.tag}: board rated {ka:g} kA.',
                                estimate=f'Estimated prospective Isc ≈ {est:.1f} kA.',
                                basis=basis, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                action='Verify with a short-circuit study; upgrade board or limit let-through.'))
        else:
            out.append(_finding(ctx, 'ELE-SC-01', 'engineering', 'minor', 'Query',
                                f'Fault level estimate within rating — {b.tag}',
                                found=f'{b.tag}: board rated {ka:g} kA.',
                                estimate=f'Estimated prospective Isc ≈ {est:.1f} kA.',
                                basis=basis + ' Margin to be confirmed by the SC study.',
                                nodes=[b], sheet=b.sheet, bbox=b.bbox))
    return out


# ── ELE-SC-02 double-ended board / tie parallel Isc ──────────────────────
def rule_sc02(ctx):
    out = []
    for b in ctx.by_kind('board'):
        inc = ctx.incomers(b)
        ties = ctx.ties(b)
        sources = {e.from_node_id for e in inc}
        if len(sources) < 2 and not ties:
            continue
        ka = ctx.val(b, 'isc_ka')
        est, notes, missing = _bus_estimate_ka(ctx, b)
        for t in ties:
            other_id = t.to_node_id if t.from_node_id == b.id else t.from_node_id
            other = next((n for n in ctx.nodes if n.id == other_id), None)
            if other is not None:
                e2, n2, m2 = _bus_estimate_ka(ctx, other)
                if e2:
                    est = (est or 0) + e2
                notes += n2
                missing += m2
        interlocked = b.norm_tag in ctx.interlock_boards
        basis = ('Both sections assumed closed in parallel (tie closed). ' + SC_BASIS +
                 (' Interlock text found on drawing.' if interlocked else
                  ' No incomer/tie interlock evidence found on the drawing.'))
        if est is None or ka is None or missing:
            out.append(_finding(ctx, 'ELE-SC-02', 'engineering', 'critical', 'Not Verifiable',
                                f'Parallel-operation fault level not verifiable — {b.tag}',
                                found=f'{b.tag}: double-ended board / tie present.',
                                basis=basis, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                estimate=('Missing input: ' + ', '.join(missing)) if missing else ''))
        elif est > ka and not interlocked:
            out.append(_finding(ctx, 'ELE-SC-02', 'engineering', 'critical', 'Non-Conformance',
                                f'Parallel Isc above rating, no interlock evidence — {b.tag}',
                                found=f'{b.tag}: rated {ka:g} kA; tie/multiple incomers.',
                                estimate=f'Combined prospective Isc ≈ {est:.1f} kA.',
                                basis=basis, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                action='Provide interlock / operating restriction or uprate the board.'))
    return out


# ── ELE-SC-03 downstream board kA < upstream bus estimate ────────────────
def rule_sc03(ctx):
    out = []
    boards = ctx.by_kind('board', 'subpanel')
    board_ids = {b.id for b in boards}
    for b in boards:
        up = [e for e in ctx.edges if e.to_node_id == b.id and e.from_node_id in board_ids]
        if not up:
            continue
        ka = ctx.val(b, 'isc_ka')
        for e in up:
            parent = next(n for n in ctx.nodes if n.id == e.from_node_id)
            est, notes, missing = _bus_estimate_ka(ctx, parent)
            if ka is None or est is None or missing:
                out.append(_finding(ctx, 'ELE-SC-03', 'engineering', 'major', 'Not Verifiable',
                                    f'Downstream withstand not verifiable — {b.tag}',
                                    found=f'{b.tag} fed from {parent.tag}.',
                                    estimate=('Missing input: ' + ', '.join(
                                        ([f'{b.tag} kA'] if ka is None else []) + missing)) or '',
                                    basis=SC_BASIS, nodes=[b, parent], sheet=b.sheet, bbox=b.bbox))
            elif ka < est:
                out.append(_finding(ctx, 'ELE-SC-03', 'engineering', 'major', 'Non-Conformance',
                                    f'Downstream board rating below upstream fault level — {b.tag}',
                                    found=f'{b.tag} rated {ka:g} kA, fed from {parent.tag}.',
                                    estimate=f'Upstream bus estimate ≈ {est:.1f} kA.',
                                    basis=SC_BASIS, nodes=[b, parent], sheet=b.sheet, bbox=b.bbox,
                                    action='Check series / current-limiting credit or uprate the downstream board.'))
    return out


# ── ELE-SC-04 generators + transformer on one bus ────────────────────────
def rule_sc04(ctx):
    out = []
    for b in ctx.by_kind('board'):
        kinds = set()
        for e in ctx.incomers(b):
            src = next((n for n in ctx.nodes if n.id == e.from_node_id), None)
            if src:
                kinds.add(src.kind)
        if 'generator' not in kinds or 'transformer' not in kinds:
            continue
        ka = ctx.val(b, 'isc_ka')
        est, notes, missing = _bus_estimate_ka(ctx, b)
        if ka is None or est is None or missing:
            out.append(_finding(ctx, 'ELE-SC-04', 'engineering', 'critical', 'Not Verifiable',
                                f'Combined gen+transformer fault level not verifiable — {b.tag}',
                                found=f'{b.tag}: generator(s) and transformer on one bus.',
                                estimate=('Missing input: ' + ', '.join(missing)) if missing else '',
                                basis=SC_BASIS, nodes=[b], sheet=b.sheet, bbox=b.bbox))
        elif est > ka:
            out.append(_finding(ctx, 'ELE-SC-04', 'engineering', 'critical', 'Non-Conformance',
                                f'Generator + transformer combined Isc above rating — {b.tag}',
                                found=f'{b.tag}: rated {ka:g} kA.',
                                estimate=f'Combined prospective Isc ≈ {est:.1f} kA.',
                                basis=SC_BASIS + (' ' + '; '.join(notes) if notes else ''),
                                nodes=[b], sheet=b.sheet, bbox=b.bbox))
    return out


# ── ELE-RAT-01 transformer FLC vs incomer / bus A ────────────────────────
def rule_rat01(ctx):
    out = []
    for tx in ctx.by_kind('transformer'):
        kva = ctx.val(tx, 'kva')
        v = ctx.val(tx, 'voltage_v')
        feeds = [e for e in ctx.edges if e.from_node_id == tx.id and e.kind == 'incomer']
        if kva is None or v is None:
            out.append(_finding(ctx, 'ELE-RAT-01', 'engineering', 'major', 'Not Verifiable',
                                f'Transformer FLC not computable — {tx.tag}',
                                found=f'{tx.tag}.',
                                estimate='Missing input: ' + ', '.join(
                                    [x for x, ok in ((f'{tx.tag} kVA', kva is None),
                                                     (f'{tx.tag} secondary voltage', v is None)) if ok]),
                                nodes=[tx], sheet=tx.sheet, bbox=tx.bbox))
            continue
        flc = kva / (SQRT3 * v)
        rating = None
        what = ''
        for e in feeds:
            if e.device_a:
                rating, what = e.device_a, f'incomer device {e.device or ""}'.strip()
                break
        if rating is None:
            for e in feeds:
                board = next((n for n in ctx.nodes if n.id == e.to_node_id), None)
                if board is not None and ctx.val(board, 'rated_a'):
                    rating, what = ctx.val(board, 'rated_a'), f'{board.tag} bus rating'
                    break
        if rating is None:
            out.append(_finding(ctx, 'ELE-RAT-01', 'engineering', 'major', 'Not Verifiable',
                                f'Incomer/bus current rating not readable — {tx.tag}',
                                found=f'{tx.tag}: FLC ≈ {flc:.0f} A.',
                                estimate='Missing input: incomer device A / bus A rating.',
                                nodes=[tx], sheet=tx.sheet, bbox=tx.bbox))
            continue
        excess = (flc - rating) / rating * 100.0
        if excess > 0:
            sev = 'minor' if excess <= 2 else 'major'
            out.append(_finding(ctx, 'ELE-RAT-01', 'engineering', sev, 'Non-Conformance',
                                f'Transformer FLC above {what} — {tx.tag}',
                                found=f'{tx.tag}: {kva:g} kVA → FLC ≈ {flc:.0f} A vs {rating:g} A ({what}).',
                                estimate=f'Overload ≈ {excess:.1f}%.',
                                basis='FLC = kVA/(√3·V) at the LV nominal voltage.',
                                nodes=[tx], sheet=tx.sheet, bbox=tx.bbox,
                                action='Check continuous rating / forced cooling / device setting.'))
    return out


# ── ELE-LD-01 bus loading ────────────────────────────────────────────────
def rule_ld01(ctx):
    out = []
    for b in ctx.by_kind('board'):
        rated = ctx.val(b, 'rated_a')
        v = ctx.val(b, 'voltage_v')
        loads, no_kw = [], []
        for e in ctx.feeders(b):
            load = next((n for n in ctx.nodes if n.id == e.to_node_id), None)
            if load is None:
                continue
            kw = ctx.val(load, 'kw')
            (loads.append(kw) if kw is not None else no_kw.append(load.tag))
        if rated is None or v is None:
            out.append(_finding(ctx, 'ELE-LD-01', 'engineering', 'major', 'Not Verifiable',
                                f'Bus loading not computable — {b.tag}',
                                found=f'{b.tag}.',
                                estimate='Missing input: ' + ', '.join(
                                    [x for x, ok in ((f'{b.tag} bus A rating', rated is None),
                                                     (f'{b.tag} voltage', v is None)) if ok]),
                                nodes=[b], sheet=b.sheet, bbox=b.bbox))
            continue
        if not loads and not no_kw:
            continue
        duty_note = 'All connected loads assumed running (no load-list duty split).'
        total = sum(loads)
        amps = total * 1000.0 / (SQRT3 * v * ctx.config.pf * ctx.config.eff)
        pct = amps / rated * 100.0
        basis = (f'I = ΣkW/(√3·V·pf·eff), pf={ctx.config.pf:g}, eff={ctx.config.eff:g}. '
                 + duty_note + (f' kW not readable for: {", ".join(no_kw)}.' if no_kw else ''))
        found = f'{b.tag}: {total:.0f} kW connected ≈ {amps:.0f} A vs {rated:g} A bus ({pct:.0f}%).'
        if pct > ctx.config.load_red:
            out.append(_finding(ctx, 'ELE-LD-01', 'engineering', 'major', 'Non-Conformance',
                                f'Bus overloaded (estimate) — {b.tag}', found=found,
                                estimate=f'Loading ≈ {pct:.0f}% (> {ctx.config.load_red:g}%).',
                                basis=basis, nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                action='Verify with the load study / demand factors.'))
        elif pct > ctx.config.load_amber:
            out.append(_finding(ctx, 'ELE-LD-01', 'engineering', 'minor', 'Query',
                                f'Bus loading above {ctx.config.load_amber:g}% (estimate) — {b.tag}',
                                found=found, estimate=f'Loading ≈ {pct:.0f}%.',
                                basis=basis, nodes=[b], sheet=b.sheet, bbox=b.bbox))
    return out


# ── ELE-GEN-01 generator capacity vs essential connected load ────────────
def rule_gen01(ctx):
    gen_fed = ctx.gen_fed_boards()
    if not gen_fed:
        return []
    out = []
    boards = [b for b in ctx.by_kind('board') if b.id in gen_fed]
    total, no_kw = 0.0, []
    for b in boards:
        for e in ctx.feeders(b):
            load = next((n for n in ctx.nodes if n.id == e.to_node_id), None)
            if load is None:
                continue
            kw = ctx.val(load, 'kw')
            if kw is None:
                no_kw.append(load.tag)
            else:  # all load on a gen-fed board rides the gen
                total += kw
    gens = [g for g in ctx.by_kind('generator')]
    cap, no_cap = 0.0, []
    for g in gens:
        kw = ctx.val(g, 'kw')
        if kw is None:
            no_cap.append(f'{g.tag} kW')
        else:
            cap += kw
    nodes = gens + boards
    if no_cap:
        out.append(_finding(ctx, 'ELE-GEN-01', 'engineering', 'critical', 'Not Verifiable',
                            'Generator capacity not readable',
                            found=f'{len(gens)} generator(s), {len(boards)} generator-fed board(s).',
                            estimate='Missing input: ' + ', '.join(no_cap),
                            basis='Engineer-Led: essential load scenario to be agreed.',
                            nodes=nodes))
        return out
    basis = ('Engineer-Led. All connected load on generator-fed boards assumed essential '
             'and simultaneous; N generators assumed available (no N-1). '
             + (f'kW not readable for: {", ".join(no_kw)}.' if no_kw else ''))
    if total > cap:
        out.append(_finding(ctx, 'ELE-GEN-01', 'engineering', 'critical', 'Query',
                            'Essential connected load above generator capacity (estimate)',
                            found=f'≈{total:.0f} kW on generator-fed boards vs {cap:.0f} kW installed.',
                            estimate=f'Utilisation ≈ {total / cap * 100:.0f}%.',
                            basis=basis, nodes=nodes,
                            action='Confirm the essential-load schedule and load-shed steps.'))
    return out


# ── ELE-FP-01 fire pump feeder ───────────────────────────────────────────
def rule_fp01(ctx):
    out = []
    for n in ctx.nodes:
        if not FIRE_PUMP_RE.search(n.description or n.tag):
            continue
        edges = [e for e in ctx.edges if e.to_node_id == n.id]
        probs = []
        if not edges:
            probs.append('no feeder found on the SLD')
        for e in edges:
            if e.kind == 'alternative':
                continue
            if e.starter is None:
                probs.append('starter type not readable')
            if not e.device:
                probs.append('dedicated feeder device not readable')
        out.append(_finding(ctx, 'ELE-FP-01', 'engineering', 'critical', 'Query',
                            f'Fire-pump feeder verification — {n.tag}',
                            found=(f'{n.tag}: ' + '; '.join(probs)) if probs
                            else f'{n.tag}: dedicated feeder and starter identified.',
                            basis=('Engineer-Led. Fire-pump feeder must be dedicated, with overload '
                                   'protection set to alarm-only (no trip) per fire-pump practice.'),
                            nodes=nodes_from_edges(ctx, n, edges), sheet=n.sheet, bbox=n.bbox,
                            action='Confirm dedicated feeder, starter type and alarm-only overload.'))
    return out


def nodes_from_edges(ctx, node, edges):
    ns = [node]
    for e in edges:
        src = next((x for x in ctx.nodes if x.id == e.from_node_id), None)
        if src:
            ns.append(src)
    return ns


# ── ELE-MOT-01 DOL starting limit ────────────────────────────────────────
def rule_mot01(ctx):
    gen_fed = ctx.gen_fed_boards()
    board_of = {}
    for b in ctx.by_kind('board'):
        for e in ctx.feeders(b):
            board_of[e.to_node_id] = b
    out = []
    for m in ctx.by_kind('motor', 'load'):
        kw = ctx.val(m, 'kw')
        if kw is None:
            continue
        board = board_of.get(m.id)
        limit = ctx.config.dol_kw_gen if (board and board.id in gen_fed) else ctx.config.dol_kw_other
        if kw <= limit:
            continue
        edge = next((e for e in ctx.edges if e.to_node_id == m.id and e.kind == 'feeder'), None)
        starter = edge.starter if edge else None
        where = 'generator-fed' if (board and board.id in gen_fed) else 'utility-fed'
        if starter is None:
            out.append(_finding(ctx, 'ELE-MOT-01', 'engineering', 'major', 'Not Verifiable',
                                f'Starter type not readable — {m.tag}',
                                found=f'{m.tag}: {kw:g} kW on a {where} board '
                                      f'(DOL limit {limit:g} kW).',
                                estimate='Missing input: starter type.',
                                nodes=[m], sheet=m.sheet, bbox=m.bbox))
        elif starter == 'DOL':
            out.append(_finding(ctx, 'ELE-MOT-01', 'engineering', 'major', 'Non-Conformance',
                                f'DOL motor above the {where} limit — {m.tag}',
                                found=f'{m.tag}: {kw:g} kW DOL.',
                                estimate=f'Limit {limit:g} kW ({where}).',
                                basis='Project DOL starting limits (SldCheckConfig).',
                                nodes=[m], sheet=m.sheet, bbox=m.bbox,
                                action='Change starter (SD/SS/VFD) or justify starting dip.'))
    return out


# ── ELE-MOT-02 duty/standby on the same bus section ──────────────────────
def rule_mot02(ctx):
    groups = {}
    for n in ctx.by_kind('motor', 'load'):
        m = re.match(r'^(.*?)([A-C])$', n.norm_tag)
        if not m:
            continue
        groups.setdefault((m.group(1), (n.description or '')[:24].upper()), []).append(n)
    board_of = {}
    for b in ctx.by_kind('board'):
        for e in ctx.feeders(b):
            board_of[e.to_node_id] = b.norm_tag
    out = []
    for (base, _desc), members in groups.items():
        if len(members) < 2:
            continue
        feeds = {board_of.get(n.id) for n in members}
        if len(feeds) == 1:
            btag = feeds.pop()
            out.append(_finding(ctx, 'ELE-MOT-02', 'engineering', 'critical', 'Query',
                                f'Duty/standby machines on one bus section — {base}…',
                                found=', '.join(n.tag for n in members) + f' all fed from {btag or "?"}',
                                basis='Duty/standby (A/B/C of one service) should be split across sections.',
                                nodes=members,
                                action='Split duty/standby across bus sections or justify.'))
    return out


# ── ELE-PROT-01 incomer/tie/HV protection ────────────────────────────────
def rule_prot01(ctx):
    out = []
    for e in ctx.edges:
        if e.kind not in ('incomer', 'tie'):
            continue
        dst = next((n for n in ctx.nodes if n.id == e.to_node_id), None)
        src = next((n for n in ctx.nodes if n.id == e.from_node_id), None)
        name = f'{src.tag if src else "?"} → {dst.tag if dst else "?"}'
        if e.relays:
            continue
        out.append(_finding(ctx, 'ELE-PROT-01', 'engineering', 'critical',
                            'Query' if e.device else 'Not Verifiable',
                            f'No protection data on {e.kind} — {name}',
                            found=f'{e.kind.title()} {name}: no ANSI relay codes or CT ratio read.',
                            basis='Incomer/tie/HV protection must be shown (ANSI codes, CT ratio).',
                            estimate='Missing input: relay settings / ANSI codes.',
                            nodes=[n for n in (src, dst) if n], sheet=e.sheet, bbox=e.bbox,
                            action='Provide the protection / relay settings schedule.'))
    return out


# ── ELE-SPR-01 spare feeders ─────────────────────────────────────────────
def rule_spr01(ctx):
    out = []
    for b in ctx.by_kind('board'):
        feeders = ctx.feeders(b)
        if len(feeders) < 4:
            continue
        spares = [e for e in feeders if SPARE_RE.search(e.feeder_no or '')
                  or SPARE_RE.search((next((n.description for n in ctx.nodes
                                            if n.id == e.to_node_id), '') or ''))]
        pct = len(spares) / len(feeders) * 100.0
        if pct < ctx.config.spare_pct:
            out.append(_finding(ctx, 'ELE-SPR-01', 'engineering', 'minor', 'Query',
                                f'Low spare feeder provision — {b.tag}',
                                found=f'{b.tag}: {len(spares)} spare of {len(feeders)} feeders ({pct:.0f}%).',
                                estimate=f'Project minimum {ctx.config.spare_pct:g}%.',
                                basis='Spare feeders counted by "spare" feeder designation.',
                                nodes=[b], sheet=b.sheet, bbox=b.bbox))
    return out


# ── ELE-FDR-01 feeder numbering duplicates / gaps ────────────────────────
def rule_fdr01(ctx):
    out = []
    for b in ctx.by_kind('board'):
        nums = []
        for e in ctx.feeders(b):
            m = re.search(r'(\d+)\s*$', e.feeder_no or '')
            if m:
                nums.append(int(m.group(1)))
        if len(nums) < 3:
            continue
        dups = sorted({n for n in nums if nums.count(n) > 1})
        gaps = sorted(set(range(min(nums), max(nums) + 1)) - set(nums))
        if dups or gaps:
            out.append(_finding(ctx, 'ELE-FDR-01', 'drafting', 'minor', 'Query',
                                f'Feeder numbering issue — {b.tag}',
                                found=f'{b.tag}: '
                                      + (f'duplicate feeder numbers {dups}. ' if dups else '')
                                      + (f'gaps {gaps[:10]}.' if gaps else ''),
                                nodes=[b], sheet=b.sheet, bbox=b.bbox,
                                action='Confirm feeder numbering with the board schedule.'))
    return out


# ── ELE-V-01 nominal voltage consistency ─────────────────────────────────
_EQUIV = ({400.0, 380.0}, {230.0, 220.0})


def rule_v01(ctx):
    volts = {round(n.voltage_v) for n in ctx.nodes
             if n.voltage_v and not n.needs_review}
    out = []
    for group in _EQUIV:
        mixed = volts & {int(v) for v in group}
        if len(mixed) > 1:
            nodes = [n for n in ctx.nodes if n.voltage_v and round(n.voltage_v) in mixed]
            out.append(_finding(ctx, 'ELE-V-01', 'drafting', 'minor', 'Query',
                                'Inconsistent nominal voltage labels',
                                found='Both ' + ' and '.join(f'{v} V' for v in sorted(mixed))
                                      + ' appear on the SLD.',
                                action='Standardise the nominal voltage designation.',
                                nodes=nodes[:6]))
    return out


# ── ELE-TF-01 transformer data completeness ──────────────────────────────
def rule_tf01(ctx):
    out = []
    for tx in ctx.by_kind('transformer'):
        missing = [label for label, ok in (
            ('kVA', ctx.val(tx, 'kva') is None),
            ('voltage ratio', ctx.val(tx, 'voltage_v') is None),
            ('vector group', not tx.vector_group),
            ('Z%', ctx.val(tx, 'z_pct') is None),
        ) if ok]
        if missing:
            out.append(_finding(ctx, 'ELE-TF-01', 'equipment', 'major', 'Query',
                                f'Transformer data incomplete — {tx.tag}',
                                found=f'{tx.tag}: missing {", ".join(missing)}.',
                                basis='SLD shall state kVA, voltage ratio, vector group and impedance.',
                                nodes=[tx], sheet=tx.sheet, bbox=tx.bbox,
                                action='Complete the transformer data block on the SLD.'))
    return out


RULES = {
    'ELE-SC-01': rule_sc01,
    'ELE-SC-02': rule_sc02,
    'ELE-SC-03': rule_sc03,
    'ELE-SC-04': rule_sc04,
    'ELE-RAT-01': rule_rat01,
    'ELE-LD-01': rule_ld01,
    'ELE-GEN-01': rule_gen01,
    'ELE-FP-01': rule_fp01,
    'ELE-MOT-01': rule_mot01,
    'ELE-MOT-02': rule_mot02,
    'ELE-PROT-01': rule_prot01,
    'ELE-SPR-01': rule_spr01,
    'ELE-FDR-01': rule_fdr01,
    'ELE-V-01': rule_v01,
    'ELE-TF-01': rule_tf01,
}

# Soft-coded: node fields each rule reads (PATCH /nodes/ re-runs the rules
# whose fields were edited).
RULE_NODE_FIELDS = {
    'ELE-SC-01': {'isc_ka', 'kva', 'z_pct', 'xd_pct', 'voltage_v'},
    'ELE-SC-02': {'isc_ka', 'kva', 'z_pct', 'xd_pct', 'voltage_v'},
    'ELE-SC-03': {'isc_ka', 'kva', 'z_pct', 'voltage_v'},
    'ELE-SC-04': {'isc_ka', 'kva', 'z_pct', 'xd_pct', 'voltage_v'},
    'ELE-RAT-01': {'kva', 'voltage_v', 'rated_a'},
    'ELE-LD-01': {'kw', 'rated_a', 'voltage_v'},
    'ELE-GEN-01': {'kw', 'essential'},
    'ELE-FP-01': {'description'},
    'ELE-MOT-01': {'kw'},
    'ELE-MOT-02': {'description', 'tag', 'norm_tag'},
    'ELE-PROT-01': set(),
    'ELE-SPR-01': {'description'},
    'ELE-FDR-01': set(),
    'ELE-V-01': {'voltage_v'},
    'ELE-TF-01': {'kva', 'voltage_v', 'vector_group', 'z_pct'},
}


def run_rules(ctx, only=None):
    """Run all (or the given) rules; returns finding dicts."""
    out = []
    for rid, fn in RULES.items():
        if only and rid not in only:
            continue
        try:
            out.extend(fn(ctx))
        except Exception:
            import logging
            logging.getLogger(__name__).exception('[SLDC] rule %s failed', rid)
    return out
