# ADR-019: Invitation and Password-Reset Tokens

Status: Accepted for MVP baseline

## Context

Invitations and reset links are bearer credentials and may appear in logs or
delivery systems.

## Decision

Generate high-entropy one-time opaque values, persist only hashes, enforce
expiry and used/revoked state, and route delivery through a notification
abstraction.

## Consequences

Database disclosure does not reveal usable links. Invitation roles are selected
by the authorized inviter and cannot be supplied by the invitee.

## Alternatives considered

Persisting raw tokens or trusting invitee role input was rejected. A generic
token table was not used because separate invitation and reset lifecycles are
clearer for this MVP.
