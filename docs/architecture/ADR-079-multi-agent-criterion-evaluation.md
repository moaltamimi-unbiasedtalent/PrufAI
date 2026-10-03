# ADR-079: Multi-agent criterion evaluation

Status: Accepted for MVP baseline

## Context

Independent evidence and criterion proposals are useful, but majority voting
would hide disagreement and make provenance ambiguous.

## Decision

The governed workflow uses bounded evidence, criterion, and consistency roles.
Outputs are strict structured data with criterion coverage, evidence spans,
confidence, uncertainty, and disagreement indicators. Final aggregation is
deterministic code.

## Consequences

Prompt injection is treated as candidate content, tool access is allow-listed,
and no hidden chain-of-thought is required or stored.

## Alternatives considered

Three unrestricted model calls averaged together and prose-parsed scores were
rejected.
