# ADR-083 Private audio storage and streaming
Status: Accepted for MVP baseline

Context: Candidate media is sensitive.
Decision: Use private object storage and authenticated application boundaries.
Consequences: Storage keys and private URLs are never public DTO fields.
Alternatives considered: Public signed URLs, rejected for the baseline.
