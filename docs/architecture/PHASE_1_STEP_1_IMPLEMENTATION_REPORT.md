# Phase 1 / Step 1 Implementation Report

**Project:** PrüfAI
**Starting commit:** `707958893ad46fa0aac12fe34207fb168dcdd525` (`chore: establish PrüfAI engineering baseline`)
**Branch:** `phase/1-identity-tenancy-rbac-audit`
**Scope:** Identity persistence, organisations, memberships, scoped RBAC, tenant context/isolation, append-oriented audit, migrations, and tests.

## 1. Executive Summary

Phase 1 / Step 1 adds the first security boundary for the modular monolith. Users, organisations, sites, memberships, roles, permissions, membership-role assignments, explicit platform-role assignments, and audit events are persisted through SQLAlchemy models and an Alembic migration. Application services require a trusted `TenantContext` and centralized capability checks before tenant-owned operations.

Authentication, sessions, password credentials, tokens, OIDC/OAuth, MFA, candidate functionality, exams, assessment, AI, RAG, billing, and administration UI remain intentionally out of scope.

## 2. Schema Implemented

Migration `0002_identity_tenancy_rbac_audit` creates:

- `users`
- `organisations`
- `organisation_sites`
- `organisation_memberships`
- `roles`
- `permissions`
- `role_permissions`
- `membership_roles`
- `platform_role_assignments`
- `audit_events`

The schema uses UUID identifiers, foreign keys, unique constraints, composite tenant/site constraints, indexes, JSON metadata, and timezone-aware timestamps.

## 3. Identity Model

`User` contains only UUID identity, email, normalized email, optional display name, status, and timestamps. Email is trimmed and case-folded before persistence; `normalized_email` is unique. No password or authentication fields were added.

## 4. Multi-Tenant Model

Organisation is the tenant boundary. Users may belong to multiple organisations through `OrganisationMembership`. Sites belong to exactly one organisation and use organisation-local slugs. Memberships are unique per user/organisation pair and support active, invited, and suspended states.

## 5. RBAC Model

Roles and permissions are separate records. Tenant roles are assigned through `MembershipRole`; platform roles are assigned through `PlatformRoleAssignment`. `RolePermission` maps role capabilities. `seed_defaults(session)` creates the predefined role and permission vocabulary idempotently without creating an administrator account.

Platform permissions use explicit `platform.*` keys. Tenant permissions use `resource.action` keys. This keeps platform authority distinguishable from tenant authority.

## 6. Authorization Architecture

`TenantContext` is an immutable trusted value containing user, organisation, membership, tenant permissions, and platform permissions. `build_tenant_context` derives it from active user status, active membership, membership roles, and active platform assignments.

`AuthorizationService.require_permission` is the centralized policy seam. Tenant operations must provide their target organisation; mismatched organisations, inactive memberships, missing permissions, and suspended users are denied.

## 7. Tenant Isolation

`OrganisationService` applies authorization before querying or mutating sites and memberships, and all tenant-owned queries include an explicit organisation predicate. Tests prove that an Organisation A administrator cannot read or mutate Organisation B resources by passing B’s ID.

## 8. Platform Authority

Platform authority is represented only by active `PlatformRoleAssignment` records pointing to platform-scoped roles. It is not inferred from an organisation ID and does not require a fake PrüfAI membership. Platform capabilities are independently tested; tenant administrators cannot create organisations or assign platform roles.

## 9. Audit Implementation

`AuditEvent` is separate from logs and AI memory. `AuditService.record` validates dot-delimited event types, structured fields, and metadata; rejects secret/password/token/API-key shaped metadata keys; and exposes no update/delete operation. Organisation creation, site creation, membership creation/status changes, tenant-role assignment, and platform-role assignment emit events.

## 10. Migration

Alembic target metadata now includes the identity/governance models. Offline PostgreSQL SQL generation passes through `0001_baseline` and `0002_identity_tenancy_rbac_audit`. Live PostgreSQL application was not possible because Docker runtime access is unavailable in this environment.

## 11. Tests

Eight API tests cover normalized email uniqueness, user status, organisation/site/membership persistence, duplicate constraints, role inheritance, missing permission denial, suspended membership denial, cross-tenant isolation, explicit platform authority, audit fields, metadata validation, and append-only service surface. Tests use SQLite with the same SQLAlchemy models because live Docker/PostgreSQL is unavailable.

## 12. Security Findings

- No credentials, passwords, tokens, or real secrets were added.
- Tenant authority requires an active membership and scoped role.
- Platform authority requires an explicit platform assignment.
- PII is limited to identity baseline fields.
- Audit metadata rejects sensitive-key-shaped entries.
- `npm audit --omit=dev`: 0 critical, 1 high, 1 moderate, 0 low. The remaining PostCSS advisory is inherited through Next.js 15.5.27; npm offers only a breaking Next.js 16.3.8 remediation, which was not applied automatically.
- Technical debt: Review Next.js major-version upgrade for inherited PostCSS security advisory.

## 13. Validation Results

| Check | Result |
|---|---|
| Frontend lint | PASS |
| Frontend typecheck | PASS |
| Frontend tests | PASS |
| Frontend production build | PASS |
| API lint/format | PASS |
| API typecheck | PASS |
| API tests | PASS — 8 tests |
| AI lint/format | PASS |
| AI typecheck | PASS |
| AI tests | PASS |
| Alembic offline migration generation | PASS |
| Docker Compose configuration | PASS |
| Live database-backed tests | UNVERIFIED — Docker socket unavailable |

## 14. Files Changed

- SQLAlchemy models under `services/api/app/models/`.
- Authorization/context/seed services under `services/api/app/authorization/`.
- Identity, organisation, membership, role-assignment, and audit services.
- Alembic migration `0002_identity_tenancy_rbac_audit.py`.
- API tests and SQLite test fixture.
- Phase 1 architecture document and ADR-011 through ADR-015.

## 15. Known Limitations

1. Authentication and session mechanics are intentionally deferred to Phase 1 / Step 2.
2. No insecure HTTP administration endpoints were added; current proof is service-level.
3. Live PostgreSQL migration/application and database-backed tests require Docker access unavailable in this environment.

## 16. Recommendations for Step 2

Connect the selected authentication/session mechanism to `TenantContext`, add route guards that consume the centralized authorization service, define token/session revocation, and audit authentication lifecycle events. Preserve the explicit platform/tenant role model and do not introduce candidate or exam domains until those controls are reviewed.
