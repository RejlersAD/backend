"""Serializers / row shapers for the SLD Checker API."""
from __future__ import annotations

DRAWING_NO_FALLBACK = 'Drawing number to confirm'

NODE_FIELDS = ('id', 'tag', 'norm_tag', 'kind', 'description', 'voltage_v',
               'rated_a', 'isc_ka', 'kva', 'kw', 'z_pct', 'xd_pct',
               'vector_group', 'essential', 'bbox', 'confidence',
               'needs_review', 'source', 'core_ref')

FINDING_FIELDS = ('id', 'number', 'rule_id', 'family', 'severity', 'result',
                  'title', 'found', 'estimate', 'basis', 'action', 'node_ids',
                  'bbox', 'checklist_items', 'confidence', 'status',
                  'engineer_note', 'core_ref')

CONFIG_FIELDS = ('dol_kw_gen', 'dol_kw_other', 'spare_pct', 'load_amber',
                 'load_red', 'xd_pct_default', 'pf', 'eff', 'conf_min')


def sheet_header(sheet):
    if sheet is None:
        return None
    return {
        'id': str(sheet.id),
        'page_index': sheet.page_index,
        'drawing_no': sheet.drawing_no or DRAWING_NO_FALLBACK,
        'drawing_no_confirmed': bool(sheet.drawing_no),
        'rev': sheet.rev,
        'title': sheet.title,
        'readability': sheet.readability,
    }


def node_row(node):
    row = {f: getattr(node, f) for f in NODE_FIELDS}
    row['id'] = str(node.id)
    row['sheet'] = str(node.sheet_id) if node.sheet_id else None
    return row


def edge_row(edge):
    return {
        'id': str(edge.id),
        'from': str(edge.from_node_id),
        'to': str(edge.to_node_id),
        'kind': edge.kind,
        'feeder_no': edge.feeder_no,
        'device': edge.device,
        'device_a': edge.device_a,
        'device_ka': edge.device_ka,
        'starter': edge.starter,
        'relays': edge.relays,
        'cable_tag': edge.cable_tag,
        'cable_size': edge.cable_size,
        'sheet': str(edge.sheet_id) if edge.sheet_id else None,
        'bbox': edge.bbox,
    }


def finding_row(f):
    row = {k: getattr(f, k) for k in FINDING_FIELDS}
    row['id'] = str(f.id)
    row['sheet'] = str(f.sheet_id) if f.sheet_id else None
    return row


def config_dict(cfg):
    return {f: getattr(cfg, f) for f in CONFIG_FIELDS}
