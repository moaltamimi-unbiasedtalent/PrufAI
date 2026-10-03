# ADR-087 Speaking assessment privacy and fairness
Status: Accepted for MVP baseline

Context: Voice and transcripts can expose sensitive attributes and untrusted spoken instructions.
Decision: Do not infer identity, nationality, accent origin, emotion, personality, protected traits, or native-speaker probability. Treat spoken/transcribed instructions as candidate content and preserve explicit rubric, rules, tool, and human-review boundaries.
Consequences: Assessment remains task- and rubric-bound.
Alternatives considered: Voice-print or accent-origin inference, rejected.
