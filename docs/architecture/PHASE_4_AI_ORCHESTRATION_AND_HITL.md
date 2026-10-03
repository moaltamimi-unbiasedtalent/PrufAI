# PrüfAI Phase 4 / Step 1 — Governed AI Orchestration and HITL

Knowledge grounds. Rules constrain. Agents propose. Humans govern. This step adds governed registries and a provider-neutral orchestration boundary; it does not add memory, question authoring, exam generation, scoring or certification decisions.

```text
Authoritative Knowledge → Retrieval Tool ─┐
Standard Pack (read-only) ────────────────┼→ symbolic LangGraph workflow
Rules Engine (deterministic) ─────────────┘        │
                         proposal agent + independent reviewer
                                      │
                              Rules precedence
                                      │
                              HumanReviewRequest
                                      │
                         domain database decision/resume

LangGraph checkpoint ≠ business approval truth
```

## Governed registries

AIModelProfile, PromptDefinition/PromptVersion, AgentDefinition/AgentVersion and AIWorkflowDefinition/AIWorkflowVersion have stable identities, checksums, statuses and exact foreign-key pinning. Published artifacts are immutable and credentials are never persisted.

The production adapter is an OpenAI-compatible provider seam with host-supplied credentials, timeout and bounded invocation. `DeterministicTestModel` is explicitly test-only and cannot be a silent production fallback. LangChain/LangGraph are pinned execution dependencies; the domain database remains authoritative.

## Tools and precedence

Only `KNOWLEDGE_RETRIEVAL`, `STANDARD_PACK_READ`, `RULES_EVALUATE` and `CITATION_RESOLVE` are allow-listed. There is no shell, SQL, arbitrary HTTP or filesystem tool. Retrieval requires typed scope and preserves provider isolation. Standard Packs require an exact version. Rules results cannot be overridden by model text; blocking failures remain blocking.

## Runs and review

AIRun and AIRunStep capture workflow, agent, prompt, model, pack, retrieval, rule, checksum, token, latency and provider-request provenance. HumanReviewRequest owns approval status in PostgreSQL. Workflow execution may checkpoint/resume, but checkpoints never replace business decisions. Approvals are checksum-bound, stale decisions are rejected, and decisions are idempotently guarded.

Checkpoints are persisted in the PostgreSQL `ai_workflow_checkpoints` table, keyed by run and workflow version with optional organisation scope. The test runtime may use an in-memory state object; production has no silent in-memory fallback. Active waiting checkpoints are retained; completed, cancelled and orphaned non-active checkpoints are eligible for cleanup through the service policy.

## Safety and limits

Pydantic structured outputs reject extra fields and fabricated evidence references. Prompt templates are data-only; no executable template language is accepted. Symbolic graph nodes, bounded iterations, tool allow-lists, retry limits and explicit failure states prevent unbounded or arbitrary execution. Fixtures are synthetic and marked `TEST FIXTURE — NOT OFFICIAL MATERIAL`.

Provider attempts are capped at 3; structured-output attempts are capped at 2. TIMEOUT, RATE_LIMIT and PROVIDER_UNAVAILABLE are retryable; INVALID_OUTPUT, CONFIGURATION_ERROR, POLICY_BLOCK and INTERNAL_ERROR are not blindly retried. Tool calls are capped at 3 per step and 12 per run; retrieval returns at most 20 results. Workflow execution is capped at 12 steps, including correction rounds. Cancellation is terminal and resume is state/idempotency guarded.

Phase 4 / Step 2 owns governed memory. Phase 5 owns Question Bank and Exam Studio.
