# Governed prompt improvement, Phase 4B.1

Open **Control Centre → Feedback & Improvements → Change candidates**. Platform
evaluation operators can create a candidate from an exact, current proposal
approved for evaluation. The proposal, independent approval receipt, confirmed
feedback, target, draft, suite and baseline run are pinned by ID and checksum.
Institution context and candidate accounts cannot access this workflow.

The first supported change clears the optional legacy instruction block of a
versioned Writing prompt. The service creates a real draft in the same prompt
definition, preserves its system template, schemas and configuration, and
never accepts a prompt body or instructions from feedback. The source prompt
stays unchanged. PostgreSQL seals the linked draft against edits, deletion and
publication; the execution profile qualifier also refuses the linked draft.

The evaluator runs the existing Writing request builder offline against an
exact, published, platform-owned original synthetic dataset. The supported
cohort consists of distinct synthetic canaries in the test or validation split.
Private and held-out evidence is refused. The run stores the exact request
digest and the measured checks for channel isolation, user input identity,
strict schema configuration and tool/storage authority. The existing
EvaluationRun, EvaluationCaseResult and dynamic regression suite preserve
the paired evidence, its denominators, missing coverage and safe differences.

The evidence is `TECHNICAL_TEST`, `TEST_ONLY`, with `model_execution=NOT_RUN`.
It measures request construction. Model output schema compliance, actual
scores, language validity, examiner agreement, fairness and provider quality
are unavailable. Synthetic criteria describe the request shape; they provide
no expert labels or empirical thresholds. A failing or incompatible comparison
cannot be approved for nonproduction validation.

An independent authorized reviewer can record a rationale and an immutable
decision on the exact evaluated receipt. Authoring, confirming the proposal,
evaluating, or otherwise contributing to the pinned regression evidence does
not confer independence. Ordinary metadata and audit events carry safe pins
and status, without request bodies, canaries, prompt text or review rationale.
Even after approval for nonproduction validation, production readiness stays
`NOT_READY`.

Existing publication and activation APIs do not consume this readiness
evidence. Integrating an authorized publication decision, eligible empirical
evidence and a controlled successor lifecycle belongs to Phase 4B.2.
