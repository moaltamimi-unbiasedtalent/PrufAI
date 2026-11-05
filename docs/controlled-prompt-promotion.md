# Controlled Writing prompt promotion, Phase 4B.2

In **Control Centre → Feedback & Improvements → Change candidates**, load the
exact candidate and open **Nonproduction promotion**. A platform human with
evaluation read, AI management and review authority can authorize and select
an eligible draft. The person must be independent of candidate and proposal
authorship, prior approval and readiness review, and evaluation execution.
They provide a rationale and pin the current readiness receipt and exact
selection head. The backend verifies the current proposal and confirmed
sources, immutable draft and comparison, complete paired technical PASS,
independent readiness decision and exact evaluation evidence.

The selection head is the latest immutable receipt for the PromptDefinition
in `NONPRODUCTION_VALIDATION`. It is separate from Writing execution profile
approval. A reversion appends a new receipt. It restores the preceding
permitted isolated selection, or clears it if there was none. It rechecks
any restored authorization and never silently uses the published baseline.
If a predecessor has lost its authorization, an independent authorizer can
explicitly clear the current selection instead of restoring it. An invalid or
revoked selection is unavailable to the validation runner,
even though its history remains readable.

The isolated runtime requires `NONPRODUCTION_PROMPT_VALIDATION_ENABLED=1`,
`NONPRODUCTION_PROMPT_VALIDATION_DATABASE` naming the actual guarded database,
and the existing test adapter opt-in. The session's PostgreSQL bind must
match the configured URL and the connected database identity. The boundary
accepts only the repository's recognized isolated local and ephemeral CI
targets. Production refuses the validation flag. The guarded browser runner
sets these options only after verifying its disposable target.

An authorized evaluator can run a technical validation against the selected
prompt, with no caller-supplied text or provider choice. The isolated Writing
runner renders the native request using the exact draft, passes its serialized
synthetic user data to the existing local deterministic adapter, and validates
the actual output with the Writing validator. Receipts preserve the exact
selection, prompt, dataset and case identities and the native request, input,
output and adapter-receipt digests. The deterministic adapter does not
interpret the trusted prompt's meaning. No model or provider is called. This
establishes request construction and technical output conformance with the
synthetic fixture, without establishing scoring quality, German assessment
validity, fairness or empirical readiness.

The normal Writing path still uses the exact rubric-authorized profile and
immutable job snapshot. Its profile resolver and existing qualifier reject a
governed draft, which remains `DRAFT`. The isolated runner creates no
candidate response, Writing execution job, assessment, score or result and
cannot hand off a proposal to the normal workflow. The published baseline and
its existing approval remain unchanged.

A production promotion request is denied with `EMPIRICAL_GATE_UNCONFIGURED`,
even for an authorized platform administrator. The denial is retained as
separate audit evidence and cannot change the isolated selection head. Only
safe pins, statuses and reason codes appear in audit and the workbench
summary. Production AI readiness is not established.
