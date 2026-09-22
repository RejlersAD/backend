"""Explicit manual phase and deliverable paths shared by draft and saved WBS."""
from hashlib import sha256


def manual_wbs(tasks):
    nodes, paths, assignments = [], {}, {}
    phase_count, deliverable_counts = 0, {}
    for task in tasks:
        phase = task.get('wbs_phase', '').strip()
        deliverable = task.get('wbs_deliverable', '').strip()
        if not phase:
            continue
        phase_path = (phase,)
        if phase_path not in paths:
            phase_count += 1
            paths[phase_path] = _node(phase_path, None, f'P{phase_count}', phase, 'phase', len(nodes))
            nodes.append(paths[phase_path])
        node = paths[phase_path]
        if deliverable:
            path = (phase, deliverable)
            if path not in paths:
                deliverable_counts[phase] = deliverable_counts.get(phase, 0) + 1
                paths[path] = _node(path, node['id'], f"{node['code']}.D{deliverable_counts[phase]}",
                                    deliverable, 'deliverable', len(nodes))
                nodes.append(paths[path])
            node = paths[path]
        assignments[task['id']] = node['id']
    return nodes, assignments


def _node(path, parent, code, name, kind, position):
    key = sha256(repr(path).encode('utf-8')).hexdigest()[:24]
    return {'id': f'manual:{key}', 'parent_id': parent, 'code': code, 'name': name,
            'kind': kind, 'discipline': '', 'sort_order': position,
            'level': len(path) - 1, 'is_derived': True}
