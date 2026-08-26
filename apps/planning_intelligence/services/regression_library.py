"""Versioned synthetic project library used by regression and performance checks."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from pathlib import Path


LIBRARY_PATH = Path(__file__).resolve().parent.parent / 'regression_projects'


def load_regression_projects():
    return [json.loads(path.read_text(encoding='utf-8')) for path in sorted(LIBRARY_PATH.glob('*.json'))]


def build_synthetic_network(spec):
    size = int(spec['network']['activity_count'])
    shape = spec['network'].get('shape', 'chain')
    nodes = list(range(size))
    if shape == 'chain':
        edges = [(index - 1, index) for index in range(1, size)]
    elif shape == 'parallel_branches':
        branches = max(1, int(spec['network'].get('branches', 10)))
        edges = []
        for index in range(1, size - 1):
            if index <= branches:
                edges.append((0, index))
            else:
                edges.append((max(1, index - branches), index))
        edges.extend((index, size - 1) for index in range(max(1, size - branches - 1), size - 1))
        edges = list(dict.fromkeys(edges))
    else:
        raise ValueError(f'Unknown network shape: {shape}')
    return nodes, edges


def validate_synthetic_network(nodes, edges):
    indegree = {node: 0 for node in nodes}
    outgoing = defaultdict(list)
    for predecessor, successor in edges:
        if predecessor not in indegree or successor not in indegree:
            raise ValueError('Regression edge references an unknown activity.')
        indegree[successor] += 1
        outgoing[predecessor].append(successor)
    starts = [node for node, degree in indegree.items() if degree == 0]
    queue = deque(starts)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for successor in outgoing[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    finishes = [node for node in nodes if not outgoing[node]]
    return {
        'activity_count': len(nodes), 'relationship_count': len(edges),
        'open_start_count': len(starts), 'open_finish_count': len(finishes),
        'has_cycle': visited != len(nodes),
    }


def run_regression_library():
    results = []
    for spec in load_regression_projects():
        nodes, edges = build_synthetic_network(spec)
        actual = validate_synthetic_network(nodes, edges)
        expected = spec['expected']
        mismatches = {key: {'expected': value, 'actual': actual.get(key)} for key, value in expected.items() if actual.get(key) != value}
        results.append({'code': spec['code'], 'passed': not mismatches, 'actual': actual, 'mismatches': mismatches})
    return results
