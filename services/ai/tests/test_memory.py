from app.memory import APPROVAL_TOOL_NAMES, LongTermMemoryProposalTool


def test_ai_memory_surface_has_no_approval_tool() -> None:
    assert APPROVAL_TOOL_NAMES == frozenset()
    captured = {}
    tool = LongTermMemoryProposalTool(lambda value: captured.update(value) or "proposal-1")
    assert tool.propose({"summary": "bounded"}, ["e1"]) == "proposal-1"
    assert captured["status"] == "REVIEW_REQUIRED"
