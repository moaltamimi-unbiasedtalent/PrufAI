from __future__ import annotations

import pytest

from app.orchestration import (
    AgentProposal,
    DeterministicTestModel,
    Evidence,
    KnowledgeRetrievalTool,
    LangGraphRuntime,
    RetrievalScope,
    StandardPackReadTool,
    canonical_checksum,
    validate_tool_policy,
)


def test_deterministic_model_is_structured_and_network_free() -> None:
    result = DeterministicTestModel().invoke([], AgentProposal)
    assert result.proposal == "Synthetic proposal"
    assert canonical_checksum({"a": 1}) == canonical_checksum({"a": 1})


def test_retrieval_tool_cannot_widen_provider_scope() -> None:
    tool = KnowledgeRetrievalTool(
        [
            Evidence("telc-1", "TELC", "synthetic telc", {}),
            Evidence("goethe-1", "GOETHE", "synthetic goethe", {}),
        ]
    )
    result = tool.search("synthetic", RetrievalScope(provider_code="TELC"))
    assert [item.evidence_id for item in result] == ["telc-1"]


def test_read_tool_requires_exact_pack_version_and_policy_is_allow_listed() -> None:
    assert StandardPackReadTool({"pack-v1": {"checksum": "x"}}).read("pack-v1")["checksum"] == "x"
    with pytest.raises(KeyError):
        StandardPackReadTool({}).read("latest")
    with pytest.raises(ValueError):
        validate_tool_policy(["SHELL", "HTTP_ANYWHERE"])


def test_symbolic_graph_is_bounded() -> None:
    runtime = LangGraphRuntime(max_steps=2)
    assert runtime.run(0, [("a", lambda value: value + 1), ("b", lambda value: value + 1)]) == 2
    with pytest.raises(ValueError):
        runtime.run(0, [("a", lambda value: value)] * 3)
