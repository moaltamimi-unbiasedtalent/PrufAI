# Governed feedback and improvement proposals

Phase 4A records observations and human decisions against existing evidence.
Open **Feedback & Improvements** in the Control Centre to select an exact
evaluation run and its measured failed check, classify the observation and
submit it for independent confirmation. The institution Writing review can
flag the exact assessment version using the selected organisation's current
review permissions.

Intake also supports exact adjudicated appeal versions and terminal worker
failure events through the institution API. It retains source references and
controlled classification and reason codes. Candidate text, audio, transcripts,
appeal rationale and held-out examples are outside the feedback payload.
Institution feedback stays in its organisation. The platform workbench
groups only compatible, confirmed platform feedback.

Each pattern snapshot pins its exact member receipts and reports the observed
member count and distinct source count. Grouping uses scope, classification,
evidence maturity, evaluation method and exact artifact versions. A pattern
count describes its recorded observations.

Proposals pin the target and pattern versions. The workbench supports exact
approved Standard Pack, rubric, prompt, workflow, model profile, transcription
profile, exam and Question Bank item targets when the source and registry can
verify the corresponding version. Optional regression suite and evaluation run
links use the existing evaluation registry. The proposal carries
`REQUIRE_EVALUATION` when a suitable suite is missing, and identifies when
approved reference evidence is required.

A different authorized person can approve the exact current proposal receipt
for evaluation. A successor to a rejected or closed proposal retains the same
identity and exact target. Release readiness stays `NOT_READY`. Publication
authority stays with the existing artifact workflows.
