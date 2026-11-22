"""Dataset-level lineage: walks, cycle detection and Mermaid rendering."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from aquila.store import Edge

Direction = Literal["up", "down", "both"]


@dataclass(frozen=True)
class LineageGraph:
    root: str
    nodes: frozenset[str]
    edges: tuple[Edge, ...]


def walk(
    edges: Iterable[Edge], root: str, *, direction: Direction = "both", depth: int = 3
) -> LineageGraph:
    if depth < 0:
        raise ValueError("depth must be >= 0")
    all_edges = tuple(edges)
    nodes: set[str] = {root}
    kept: set[Edge] = set()
    for go_up in (True, False):
        if (go_up and direction == "down") or (not go_up and direction == "up"):
            continue
        frontier = {root}
        seen = {root}
        for _ in range(depth):
            nxt: set[str] = set()
            for edge in all_edges:
                near, far = (
                    (edge.downstream_id, edge.upstream_id)
                    if go_up
                    else (edge.upstream_id, edge.downstream_id)
                )
                if near in frontier:
                    kept.add(edge)
                    if far not in seen:
                        nxt.add(far)
            seen |= nxt
            nodes |= nxt
            frontier = nxt
            if not frontier:
                break
    ordered = tuple(sorted(kept, key=lambda e: (e.via, e.upstream_id, e.downstream_id)))
    return LineageGraph(root, frozenset(nodes), ordered)


def find_cycles(edges: Iterable[Edge]) -> list[list[str]]:
    """Groups of datasets that feed each other (strongly connected components).

    Linear time (iterative Tarjan), so a wide DAG with many diamonds stays cheap.
    Each group is sorted; a self-loop counts as a group of one.
    """

    graph: dict[str, list[str]] = {}
    self_loops: set[str] = set()
    for edge in edges:
        graph.setdefault(edge.upstream_id, []).append(edge.downstream_id)
        graph.setdefault(edge.downstream_id, [])
        if edge.upstream_id == edge.downstream_id:
            self_loops.add(edge.upstream_id)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    groups: list[list[str]] = []
    counter = 0
    for start in sorted(graph):
        if start in index:
            continue
        work: list[tuple[str, int]] = [(start, 0)]
        while work:
            node, child = work.pop()
            if child == 0:
                index[node] = low[node] = counter
                counter += 1
                stack.append(node)
                on_stack.add(node)
            children = sorted(graph[node])
            if child < len(children):
                work.append((node, child + 1))
                nxt = children[child]
                if nxt not in index:
                    work.append((nxt, 0))
                elif nxt in on_stack:
                    low[node] = min(low[node], index[nxt])
                continue
            if low[node] == index[node]:
                group: list[str] = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    group.append(member)
                    if member == node:
                        break
                if len(group) > 1 or node in self_loops:
                    groups.append(sorted(group))
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return sorted(groups)


def _label(text: str) -> str:
    # Mermaid numeric entities keep user-controlled names and error text inside labels.
    return "".join(
        f"#{ord(ch)};" if ch in '#"<>&|[]{}\\' else ch for ch in " ".join(text.splitlines())
    )


def to_mermaid(
    graph: LineageGraph,
    names: dict[str, str],
    publications: dict[str, dict[str, Any]] | None = None,
) -> str:
    lines = ["flowchart LR"]
    for node in sorted(graph.nodes, key=lambda n: names.get(n, n)):
        label = _label(names.get(node, node))
        publication = (publications or {}).get(node)
        if publication:
            if publication["published_at"]:
                published = _label(publication["published_at"])
                label += f"<br/>Published: {published} ({publication['rows']} rows)"
                if publication["period_start"] is not None:
                    start = _label(publication["period_start"])
                    end = _label(publication["period_end"])
                    label += f"<br/>Data period: {start} – {end}"
                else:
                    label += "<br/>Data period unavailable"
            else:
                label += "<br/>No successful publication"
            attempt = publication["latest_attempt"]
            status = attempt["status"]
            detail = f"Latest apply: {status}<br/>{_label(attempt['at'])}"
            if attempt["error"]:
                detail += "<br/>" + _label(attempt["error"][:200])
            lines.append(f'  a{node}["{detail}"]:::{status}')
            lines.append(f"  a{node} -.->|attempt status| n{node}")
        style = ":::root" if node == graph.root else ""
        lines.append(f'  n{node}["{label}"]{style}')
    for edge in graph.edges:
        via = "dataset apply" if edge.via.startswith("dataset:apply-") else edge.via
        lines.append(f"  n{edge.upstream_id} -->|{via}| n{edge.downstream_id}")
    if publications:
        lines.extend(
            [
                "  classDef failed fill:#fee2e2,color:#7f1d1d,stroke:#b91c1c",
                "  classDef succeeded fill:#dcfce7,color:#14532d,stroke:#15803d",
            ]
        )
    lines.append("  classDef root stroke-width:3px")
    return "\n".join(lines)
