"""Bounded deterministic work-graph value objects.

The work graph is the harness-side representation of decomposed work. Until
PR #62 merges, graphs are produced by the deterministic
:class:`app.autonomy.fixtures.FixtureDecomposer`; the production
decomposition service is expected to produce an equivalent bounded graph
through :class:`app.autonomy.ports.DecompositionPort` without changing this
module.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

NODE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$"


class GraphValidationError(ValueError):
    """Raised when a work graph violates structural bounds."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class WorkNode(BaseModel):
    """One bounded unit of specialist work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(pattern=NODE_ID_PATTERN)
    title: str = Field(min_length=1, max_length=200)
    capability: str = Field(min_length=1, max_length=120)
    depends_on: tuple[str, ...] = Field(default=())
    summary_hint: str = Field(default="", max_length=500)


class WorkGraph(BaseModel):
    """Validated directed-acyclic graph of specialist work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nodes: tuple[WorkNode, ...] = Field(min_length=1)

    def by_id(self, node_id: str) -> WorkNode:
        for node in self.nodes:
            if node.node_id == node_id:
                return node
        raise KeyError(f"unknown work node: {node_id}")

    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.node_id for node in self.nodes)

    def validate_structure(self, *, max_tasks: int, max_depth: int) -> None:
        """Validate size, dependency closure, acyclicity, and depth.

        Raises:
            GraphValidationError: with a machine-readable ``code``.
        """
        if len(self.nodes) > max_tasks:
            raise GraphValidationError(
                "graph_too_large",
                f"{len(self.nodes)} nodes exceed the limit of {max_tasks}",
            )
        seen: set[str] = set()
        for node in self.nodes:
            if node.node_id in seen:
                raise GraphValidationError(
                    "duplicate_node_id", f"duplicate node id: {node.node_id}"
                )
            seen.add(node.node_id)
        for node in self.nodes:
            for dependency in node.depends_on:
                if dependency == node.node_id:
                    raise GraphValidationError(
                        "self_dependency", f"node depends on itself: {node.node_id}"
                    )
                if dependency not in seen:
                    raise GraphValidationError(
                        "unknown_dependency",
                        f"node {node.node_id} depends on unknown node {dependency}",
                    )
        order = self._topological_order()
        if order is None:
            raise GraphValidationError("dependency_cycle", "work graph contains a cycle")
        depth = self._depth(order)
        if depth > max_depth:
            raise GraphValidationError(
                "graph_too_deep",
                f"dependency depth {depth} exceeds the limit of {max_depth}",
            )

    def topological_order(self) -> tuple[str, ...]:
        """Deterministic topological order (alphabetical tie-break)."""
        order = self._topological_order()
        if order is None:
            raise GraphValidationError("dependency_cycle", "work graph contains a cycle")
        return tuple(order)

    def depth(self) -> int:
        return self._depth(self.topological_order())

    def ready(
        self, completed: frozenset[str] | set[str], failed: frozenset[str] | set[str]
    ) -> tuple[WorkNode, ...]:
        """Nodes whose dependencies are all completed, in deterministic order.

        Failed nodes poison their dependents: a node with any failed
        (transitive or direct) dependency is never reported ready. Callers
        treat poisoned-but-incomplete nodes as deterministically blocked.
        """
        completed_set = set(completed)
        blocked = set(failed)
        changed = True
        while changed:
            changed = False
            for node in self.nodes:
                if node.node_id in blocked or node.node_id in completed_set:
                    continue
                if any(dep in blocked for dep in node.depends_on):
                    blocked.add(node.node_id)
                    changed = True
        ready_nodes = [
            node
            for node in self.nodes
            if node.node_id not in completed_set
            and node.node_id not in blocked
            and all(dep in completed_set for dep in node.depends_on)
        ]
        ready_nodes.sort(key=lambda node: node.node_id)
        return tuple(ready_nodes)

    def _topological_order(self) -> list[str] | None:
        dependents: dict[str, list[str]] = {node.node_id: [] for node in self.nodes}
        pending: dict[str, int] = {}
        for node in self.nodes:
            pending[node.node_id] = len(node.depends_on)
            for dependency in node.depends_on:
                if dependency in dependents:
                    dependents[dependency].append(node.node_id)
        available = sorted(node_id for node_id, count in pending.items() if count == 0)
        order: list[str] = []
        while available:
            node_id = available.pop(0)
            order.append(node_id)
            for dependent in sorted(dependents[node_id]):
                pending[dependent] -= 1
                if pending[dependent] == 0:
                    available.append(dependent)
            available.sort()
        if len(order) != len(self.nodes):
            return None
        return order

    def _depth(self, order: list[str] | tuple[str, ...]) -> int:
        node_depth: dict[str, int] = {}
        for node_id in order:
            node = self.by_id(node_id)
            if not node.depends_on:
                node_depth[node_id] = 1
            else:
                node_depth[node_id] = 1 + max(
                    node_depth[dependency] for dependency in node.depends_on
                )
        return max(node_depth.values(), default=0)
