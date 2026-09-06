from app.team_selection.service import TeamSelectionService


def test_select_specialists_minimal_team():
    service = TeamSelectionService(None, None, None, None)  # type: ignore
    active_workforce = [
        {
            "id": "A",
            "agent_type": "specialist",
            "stable_key": "A",
            "capabilities": ["research.market", "business.strategy"],
        },
        {
            "id": "B",
            "agent_type": "specialist",
            "stable_key": "B",
            "capabilities": ["business.financial-analysis"],
        },
        {
            "id": "C",
            "agent_type": "specialist",
            "stable_key": "C",
            "capabilities": ["research.market"],
        },
        {
            "id": "D",
            "agent_type": "specialist",
            "stable_key": "D",
            "capabilities": ["software.backend"],
        },
    ]
    required = ["research.market", "business.strategy", "business.financial-analysis"]

    # Should pick A and B
    selected, missing = service._select_specialists(required, active_workforce, [])
    assert not missing
    ids = {s["id"] for s in selected}
    assert ids == {"A", "B"}


def test_select_specialists_hierarchy():
    service = TeamSelectionService(None, None, None, None)  # type: ignore
    active_workforce = [
        {
            "id": "A",
            "agent_type": "specialist",
            "stable_key": "A",
            "capabilities": ["software.backend.api"],
        },
    ]
    required = ["software.backend"]
    selected, missing = service._select_specialists(required, active_workforce, [])
    assert not missing
    assert selected[0]["id"] == "A"


def test_select_specialists_tie_breaker():
    service = TeamSelectionService(None, None, None, None)  # type: ignore
    active_workforce = [
        {
            "id": "A",
            "agent_type": "specialist",
            "stable_key": "A",
            "capabilities": ["software.backend", "research.market", "business.strategy"],
        },  # 3 total caps
        {
            "id": "B",
            "agent_type": "specialist",
            "stable_key": "B",
            "capabilities": ["software.backend"],
        },  # 1 total cap
        {
            "id": "C",
            "agent_type": "specialist",
            "stable_key": "C",
            "capabilities": ["software.backend", "research.market"],
        },  # 2 caps
    ]
    required = ["software.backend"]
    # It should prefer the one with the fewest total capabilities (B)
    selected, missing = service._select_specialists(required, active_workforce, [])
    assert not missing
    assert selected[0]["id"] == "B"


def test_missing_capability():
    service = TeamSelectionService(None, None, None, None)  # type: ignore
    active_workforce = [
        {
            "id": "A",
            "agent_type": "specialist",
            "stable_key": "A",
            "capabilities": ["software.backend"],
        },
    ]
    required = ["software.backend", "business.strategy"]
    selected, missing = service._select_specialists(required, active_workforce, [])
    assert missing == ["business.strategy"]
