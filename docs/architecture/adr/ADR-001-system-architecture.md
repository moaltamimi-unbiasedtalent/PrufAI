# ADR: System architecture

Status: Accepted for MVP baseline

+## Context

PrüfAI needs many future domains but is currently greenfield.

## Decision

Adopt a modular monolith initially, with independently runnable AI/background services where scaling requires it.

## Consequences

Keeps deployment and ownership simple while preserving extraction seams.

## Alternatives considered

A large microservice architecture; a single unstructured application.
