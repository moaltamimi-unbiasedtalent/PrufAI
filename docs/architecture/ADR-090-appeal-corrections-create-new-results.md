# ADR-090: Appeal corrections create new results

Status: Accepted for MVP baseline

An upheld appeal requests a governed component correction and a new scoring/result
version. `ResultCorrection` records the source result checksum, appeal version,
operation id, component/transcription successor, scoring run, and resulting
version. Writing and speaking corrections use the existing versioned subjective
assessment correction service. Transcription correction requires an explicit
governed transcript-version factory. Objective correction fails closed until a
new governed objective source exists. Historical runs and results remain
immutable and retrievable; the new result remains review-required and is never
auto-released by this workflow.
