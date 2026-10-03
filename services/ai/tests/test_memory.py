from app.memory import (
    APPROVAL_TOOL_NAMES,
    LONG_TERM_MEMORY_PROPOSE,
    LongTermMemoryProposalTool,
    MemoryPolicy,
)


def test_ai_memory_surface_has_no_approval_tool() -> None:
    assert APPROVAL_TOOL_NAMES == frozenset()
    captured = {}
    tool = LongTermMemoryProposalTool(lambda value: captured.update(value) or "proposal-1")
    assert tool.propose({"summary": "bounded"}, ["e1"]) == "proposal-1"
    assert captured["status"] == "REVIEW_REQUIRED"


def test_memory_capabilities_are_explicit_per_agent() -> None:
    policy = MemoryPolicy(long_term_propose=True, allowed_categories=frozenset({"QUALITY_PATTERN"}))
    assert policy.allows(LONG_TERM_MEMORY_PROPOSE)
    assert not policy.allows("memory.approve")
