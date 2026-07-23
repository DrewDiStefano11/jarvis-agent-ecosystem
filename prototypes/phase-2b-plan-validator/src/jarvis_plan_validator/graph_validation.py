from typing import List, Tuple, Dict
from graphlib import TopologicalSorter, CycleError
from .contracts import ExecutionPlan

def validate_dependency_graph(plan: ExecutionPlan) -> Tuple[bool, List[str], List[List[str]], List[str]]:
    """
    Validates the plan's DAG.
    Returns: (is_valid, topological_order, cycles, errors)
    """
    step_ids = [step.step_id for step in plan.steps]

    errors = []
    cycles = []
    topological_order = []

    # Check for duplicate step IDs
    if len(step_ids) != len(set(step_ids)):
        errors.append("Duplicate step IDs detected")
        return False, [], [], errors

    graph = {}
    for step in plan.steps:
        graph[step.step_id] = set(step.depends_on)
        for dep in step.depends_on:
            if dep not in step_ids:
                errors.append(f"Step '{step.step_id}' depends on unknown step '{dep}'")
            if dep == step.step_id:
                errors.append(f"Step '{step.step_id}' depends on itself")

    if errors:
         return False, [], [], errors

    sorter = TopologicalSorter(graph)
    try:
        topological_order = list(sorter.static_order())
    except CycleError as e:
        # e.args[1] typically contains the cycle tuple
        cycle_nodes = list(e.args[1])
        cycles.append(cycle_nodes)
        errors.append(f"Cycle detected: {' -> '.join(cycle_nodes)}")
        return False, [], cycles, errors

    # Check for multiple terminal steps without aggregation (heuristic)
    # A step is terminal if nothing depends on it.
    has_dependent = {s: False for s in step_ids}
    for step in plan.steps:
        for dep in step.depends_on:
            has_dependent[dep] = True

    terminals = [s for s, has_dep in has_dependent.items() if not has_dep]
    if len(terminals) > 1:
        # Check if there is an explicit artifact_assembly step
        assembly_steps = [s.step_id for s in plan.steps if s.step_type == "artifact_assembly"]
        if not assembly_steps:
             # Just a warning/error depending on strictness. Let's make it an error for safety per requirements.
             pass # "Multiple terminal steps without an explicit aggregation step"
             # The requirements say "Detect... Multiple terminal steps without an explicit aggregation step"
             errors.append("Multiple terminal steps detected without an explicit aggregation step")
             return False, topological_order, [], errors

    root_steps = [s.step_id for s in plan.steps if not s.depends_on]
    if not root_steps and plan.steps:
        errors.append("No root steps detected (all steps have dependencies)")
        return False, topological_order, [], errors

    return True, topological_order, cycles, errors
