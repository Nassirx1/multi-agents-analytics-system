from __future__ import annotations

from collections import deque
from typing import Any


def clean_tree_text(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split())


def tree_node_id(node: dict[str, Any], index: int) -> str:
    for key in ("id", "node_id", "name"):
        text = clean_tree_text(node.get(key))
        if text:
            return text
    return f"node_{index}"


def build_tree_layout(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    max_nodes: int = 15,
    max_edges: int = 20,
) -> dict[str, Any]:
    ordered_nodes = [dict(node) for node in nodes[:max_nodes]]
    if not ordered_nodes:
        return {"nodes": [], "edges": [], "positions": {}, "depths": {}, "max_depth": 0}

    id_by_index: list[str] = []
    seen_ids: set[str] = set()
    for index, node in enumerate(ordered_nodes):
        node_id = tree_node_id(node, index)
        if node_id in seen_ids:
            node_id = f"{node_id}_{index}"
        node["_layout_id"] = node_id
        id_by_index.append(node_id)
        seen_ids.add(node_id)

    valid_ids = set(id_by_index)
    normalized_edges: list[dict[str, str]] = []
    for edge in edges[:max_edges]:
        source = _edge_endpoint(edge, ("source", "from", "parent"))
        target = _edge_endpoint(edge, ("target", "to", "child"))
        if source in valid_ids and target in valid_ids and source != target:
            normalized_edges.append({"source": source, "target": target, "label": clean_tree_text(edge.get("label"))})

    if not normalized_edges and len(id_by_index) > 1:
        root_id = _root_id(ordered_nodes, normalized_edges, id_by_index)
        for node_id in id_by_index:
            if node_id != root_id:
                normalized_edges.append({"source": root_id, "target": node_id, "label": ""})

    root_id = _root_id(ordered_nodes, normalized_edges, id_by_index)
    children: dict[str, list[str]] = {node_id: [] for node_id in id_by_index}
    for edge in normalized_edges:
        children.setdefault(edge["source"], []).append(edge["target"])

    depths = _depths_from_edges(root_id, children, ordered_nodes, id_by_index)
    max_depth = max(depths.values(), default=0)
    leaf_counts: dict[str, int] = {}

    def count_leaves(node_id: str, visiting: set[str] | None = None) -> int:
        visiting = visiting or set()
        if node_id in visiting:
            return 1
        child_ids = [child for child in children.get(node_id, []) if child in valid_ids]
        if not child_ids:
            leaf_counts[node_id] = 1
            return 1
        visiting.add(node_id)
        total = sum(count_leaves(child, visiting) for child in child_ids)
        visiting.remove(node_id)
        leaf_counts[node_id] = max(total, 1)
        return leaf_counts[node_id]

    for node_id in id_by_index:
        count_leaves(node_id)

    positions: dict[str, tuple[float, float]] = {}
    cursor = 0.0

    def assign_x(node_id: str, visiting: set[str] | None = None) -> float:
        nonlocal cursor
        visiting = visiting or set()
        if node_id in visiting:
            positions[node_id] = (cursor, float(depths.get(node_id, 0)))
            cursor += 1.0
            return positions[node_id][0]
        child_ids = [child for child in children.get(node_id, []) if child in valid_ids]
        visiting.add(node_id)
        if child_ids:
            child_x = [assign_x(child, visiting) for child in child_ids]
            x_value = sum(child_x) / len(child_x)
        else:
            x_value = cursor
            cursor += 1.0
        visiting.remove(node_id)
        positions[node_id] = (x_value, float(depths.get(node_id, 0)))
        return x_value

    assign_x(root_id)
    for node_id in id_by_index:
        if node_id not in positions:
            positions[node_id] = (cursor, float(depths.get(node_id, 0)))
            cursor += 1.0

    max_x = max((pos[0] for pos in positions.values()), default=0.0)
    min_x = min((pos[0] for pos in positions.values()), default=0.0)
    x_span = max(max_x - min_x, 1.0)
    y_span = max(float(max_depth), 1.0)
    normalized_positions = {
        node_id: ((x - min_x) / x_span, y / y_span if max_depth else 0.0)
        for node_id, (x, y) in positions.items()
    }
    return {
        "nodes": ordered_nodes,
        "edges": normalized_edges,
        "positions": normalized_positions,
        "depths": depths,
        "max_depth": max_depth,
        "root_id": root_id,
    }


def _root_id(nodes: list[dict[str, Any]], edges: list[dict[str, str]], ids: list[str]) -> str:
    targets = {edge["target"] for edge in edges}
    for index, node in enumerate(nodes):
        try:
            depth = int(node.get("depth", 0))
        except (TypeError, ValueError):
            depth = 0
        if depth == 0 and ids[index] not in targets:
            return ids[index]
    for node_id in ids:
        if node_id not in targets:
            return node_id
    return ids[0]


def _edge_endpoint(edge: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        text = clean_tree_text(edge.get(key))
        if text:
            return text
    return ""


def _depths_from_edges(
    root_id: str,
    children: dict[str, list[str]],
    nodes: list[dict[str, Any]],
    ids: list[str],
) -> dict[str, int]:
    depths: dict[str, int] = {root_id: 0}
    queue: deque[str] = deque([root_id])
    while queue:
        node_id = queue.popleft()
        for child in children.get(node_id, []):
            if child in depths:
                continue
            depths[child] = depths[node_id] + 1
            queue.append(child)
    for index, node_id in enumerate(ids):
        if node_id in depths:
            continue
        try:
            depths[node_id] = max(int(nodes[index].get("depth", 0)), 0)
        except (TypeError, ValueError):
            depths[node_id] = 0
    return depths
