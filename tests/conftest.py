"""Spolecne fixtures. Kod je v src/ bez balicku, takze se pridava na sys.path."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture
def line_graph():
    """Tovarna na maly pesi graf: uzly na zadanych souradnicich spojene hranami.

    Skutecny graf z osmnx ma 400 tis. hran a nacita se pul minuty - itinerar se
    proto testuje na rucne postavenych grafech, kde je znama presna geometrie.
    """
    import networkx as nx

    def build(nodes, edges):
        """nodes: {id: (lat, lon)}, edges: [(u, v, {tagy})] - obousmerne."""
        graph = nx.MultiDiGraph()
        graph.graph["crs"] = "epsg:4326"
        for node_id, (lat, lon) in nodes.items():
            graph.add_node(node_id, y=lat, x=lon)
        for u, v, data in edges:
            data = dict(data)
            data.setdefault("length", _distance_m(nodes[u], nodes[v]))
            data.setdefault("highway", "residential")
            graph.add_edge(u, v, **data)
            graph.add_edge(v, u, **dict(data))
        return graph

    return build


def _distance_m(a, b):
    from routing import haversine_m

    return haversine_m(a[0], a[1], b[0], b[1])
