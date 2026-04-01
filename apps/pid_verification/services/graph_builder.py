"""
Graph Construction Service
============================
Builds a NetworkX directed graph per drawing.
  Nodes : instruments, valves, equipment
  Edges : pipelines connecting nodes
  Attrs : tag, type, line_size
"""
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def build_graph(extraction: Dict[str, Any]):
    """
    Build and return a NetworkX DiGraph from the extraction result.
    Falls back to a plain dict if networkx is not installed.
    """
    try:
        import networkx as nx
        G = nx.DiGraph()
        _add_nodes(G, extraction)
        _add_edges(G, extraction)
        logger.debug('[GraphBuilder] nodes=%d edges=%d', G.number_of_nodes(), G.number_of_edges())
        return G
    except ImportError:
        logger.warning('[GraphBuilder] networkx not installed – returning stub graph')
        return _stub_graph(extraction)


def _add_nodes(G, extraction):
    for item in extraction.get('instruments', []):
        G.add_node(item['tag'], kind='instrument', type=item['type'])

    for item in extraction.get('valves', []):
        G.add_node(item['tag'], kind='valve', type=item['type'])

    for item in extraction.get('equipment', []):
        G.add_node(item['tag'], kind='equipment', type=item['type'])


def _add_edges(G, extraction):
    for pipeline in extraction.get('pipelines', []):
        src  = pipeline.get('from')
        dst  = pipeline.get('to')
        size = pipeline.get('size', '')
        lid  = pipeline.get('line_id', '')
        if src and dst and G.has_node(src) and G.has_node(dst):
            G.add_edge(src, dst, line_id=lid, size=size)


def _stub_graph(extraction):
    """Minimal dict representation when networkx is unavailable."""
    nodes = (
        [i['tag'] for i in extraction.get('instruments', [])] +
        [v['tag'] for v in extraction.get('valves', [])] +
        [e['tag'] for e in extraction.get('equipment', [])]
    )
    return {'nodes': nodes, 'edges': []}


def get_isolated_nodes(graph) -> list:
    """Return nodes with no edges (potential orphan elements)."""
    try:
        import networkx as nx
        if isinstance(graph, nx.DiGraph):
            return [n for n in graph.nodes() if graph.degree(n) == 0]
    except ImportError:
        pass
    if isinstance(graph, dict):
        return graph.get('nodes', [])   # All are isolated in stub mode
    return []
