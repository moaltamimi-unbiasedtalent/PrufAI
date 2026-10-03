# Phase 4 / Step 1 Implementation Report

## Executive summary

Added governed AI registries, exact version provenance, bounded provider-neutral orchestration, constrained tools, structured outputs, synthetic deterministic execution and domain-owned human review records. No memory, question generation, exam runtime, scoring or certification decision was added.

## Schema and migration

Migration `0009_ai_orchestration` adds model profiles, prompt definitions/versions, agent definitions/versions, workflow definitions/versions, AI runs/steps and HumanReviewRequest. Published artifacts are immutable by service guard; checksums cover substantive configuration. Revision length is 20 characters.

## Runtime and safety

LangChain Core `0.3.79` and LangGraph `0.6.7` are pinned for the AI worker boundary. The deterministic test model is network-free and explicit. Tools are allow-listed, retrieval scopes are enforced before model access, Standard Pack reads require exact versions, and Rules results retain precedence. Pydantic models reject malformed or extra output fields.

## Human governance

AI runs enter `WAITING_FOR_HUMAN` and create a domain HumanReviewRequest. Approval, rejection and change requests update business state; checkpoint state is only a computation resume cursor. Review checksums prevent stale approval and status guards prevent double decisions.

## Validation and limitations

Local AI tests, API tests, lint and targeted mypy are available. PostgreSQL and remote CI remain external gates. A full production provider call is intentionally not exercised in CI. Memory governance is deferred to Step 2; Question Bank and Exam Studio remain Phase 5.
