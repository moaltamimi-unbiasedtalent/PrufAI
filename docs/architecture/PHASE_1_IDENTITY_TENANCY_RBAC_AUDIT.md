# Phase 1 / Step 1: Identity, Tenancy, RBAC, and Audit Foundation

**Status:** Accepted for MVP foundation
**Scope:** Persistence and authorization boundaries only. Authentication/session mechanics remain Step 2 work.

## 1. Identity model

`User` represents an identity that may authenticate later. It contains only `id`, `email`, `normalized_email`, `display_name`, `status`, and timestamps. IDs are UUID-based. Email normalization uses trimmed Unicode case-folding and the database enforces uniqueness on `normalized_email`.

User statuses are `ACTIVE`, `INVITED`, `SUSPENDED`, and `DISABLED`. There is intentionally no password hash, credential, token, OAuth, OIDC, MFA, or login UI in this step.

```text
User (identity only)
  ├── OrganisationMembership[]
  └── PlatformRoleAssignment[]
```

## 2. Organisation model

`Organisation` is the tenant boundary. It has a name, globally unique slug, type, status, and timestamps. Types include PrüfAI, language school, integration provider, university, test centre, training provider, enterprise, and other. Statuses are `ACTIVE`, `SUSPENDED`, and `ARCHIVED`.

`OrganisationSite` belongs to exactly one organisation through a foreign key. Its slug is unique within that organisation, not globally.

## 3. Membership model

`OrganisationMembership` is the many-to-many relationship between users and organisations. A user can belong to multiple organisations. The database enforces one membership row per `(organisation_id, user_id)` pair. Membership statuses are `ACTIVE`, `INVITED`, and `SUSPENDED`.

```text
User ──< OrganisationMembership >── Organisation ──< OrganisationSite
                 │
                 └──< MembershipRole
```

## 4. Role model

Roles are explicit records with a globally unique key and a scope:

- Platform: `PRUFAI_SUPER_ADMIN`, `PRUFAI_STANDARDS_ADMIN`, `PRUFAI_AI_ADMIN`.
- Tenant: `INSTITUTION_ADMIN`, `EXAM_MANAGER`, `EXAM_AUTHOR`, `REVIEWER`, `EXAMINER`, `INVIGILATOR`, `TEACHER`.

Tenant roles are assigned through `MembershipRole`. Platform roles are assigned through `PlatformRoleAssignment`; a fake PrüfAI membership is not required to confer platform authority.

## 5. Permission model

Permissions are separate records using `resource.action` keys. Tenant capabilities include `organisation.read`, `organisation.manage`, `site.read`, `site.manage`, `membership.read`, `membership.manage`, `user.read`, `user.manage`, and `audit.read`.

Platform capabilities are explicitly prefixed (`platform.organisation.manage`, `platform.audit.read`, and so on) so platform authority cannot be inferred from a tenant ID. `RolePermission` maps roles to permissions.

```text
User
  ↓
OrganisationMembership ──> MembershipRole ──> Role ──> RolePermission ──> Permission

User ──> PlatformRoleAssignment ──> Role(scope=PLATFORM)
```

`seed_defaults(session)` creates the predefined permission vocabulary, roles, and role mappings repeatably. It creates no user or administrator account.

## 6. Platform-role model

Platform scope is explicit and independent of organisation IDs. A platform role assignment is active only when its own status is active and the assigned role has `scope=PLATFORM`. A platform context may operate across organisations only through explicit platform permissions.

A tenant administrator with `organisation.manage` cannot create organisations, assign platform roles, or operate on another organisation. Platform authority does not arise from a special organisation identifier.

## 7. Tenant context

`TenantContext` is a trusted, immutable value constructed by `build_tenant_context(session, user_id, organisation_id)`. It contains:

- `user_id`
- `organisation_id` (nullable only for platform-wide contexts)
- `membership_id` (nullable for platform-only contexts)
- tenant permission keys
- platform permission keys

There is no login/session yet. Tests and trusted application services construct contexts directly. Production HTTP code must not accept arbitrary user or organisation IDs as a substitute for authentication.

## 8. Tenant isolation strategy

`AuthorizationService.require_permission(context, permission, organisation_id)` is the centralized policy entry point. Tenant operations must pass their target organisation to it before querying or mutating data. The service rejects mismatched organisation IDs, inactive memberships, missing permissions, and suspended users.

Tenant-owned queries also include an explicit organisation predicate. The site and membership services demonstrate this pattern. A platform permission may cross tenant boundaries only when the context contains the corresponding explicit platform capability.

## 9. Authorization flow

```text
trusted authenticated identity (future Step 2)
                 │
                 v
      build_tenant_context(...)
                 │
                 v
 route/application service
                 │
                 v
 AuthorizationService.require_permission(...)
                 │
       ┌─────────┴─────────┐
       v                   v
 tenant membership     platform assignment
 + tenant role         + platform role
       │                   │
       └─────────┬─────────┘
                 v
 scoped repository/service operation
                 │
                 v
          AuditService.record(...)
```

Routes do not perform arbitrary SQLAlchemy queries. The current proof is service-level because authentication is intentionally deferred.

## 10. Audit architecture

`AuditEvent` is separate from application logs and AI memory. It records timestamp, actor type/id, optional organisation, event/entity identity, version and workflow/request references, reason, JSON metadata, and optional state hashes. Supported actor types are `HUMAN`, `AI_AGENT`, and `SYSTEM_SERVICE`.

`AuditService.record` validates event names and structured fields, rejects metadata keys that could contain secrets, and is the only application write path. It has no update or delete operation. Organisation creation, site creation, membership creation/status changes, role assignment, and platform-role assignment emit events.

## 11. Database tables

The `0002_identity_tenancy_rbac_audit` Alembic migration creates:

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

UUID primary keys, foreign keys, unique constraints, composite membership/site constraints, indexes, JSON metadata, and timezone-aware timestamps are included. No candidate, exam, assessment, or AI tables are created.

## 12. Security boundaries

- PII is minimized to platform identity fields.
- No credentials or secrets are persisted.
- Tenant membership and role scope are required for tenant operations.
- Platform authority is explicit and separately assigned.
- Audit metadata rejects password/secret/token/API-key shaped keys.
- Audit events are append-oriented at the application service boundary.
- Foreign keys and uniqueness constraints enforce core integrity in the database.

## 13. Known limitations

1. Authentication, sessions, token issuance/revocation, OIDC/OAuth, and MFA are Step 2 work.
2. Database-backed tests use SQLite because Docker/PostgreSQL runtime access is environment-limited; Alembic PostgreSQL SQL is validated offline.
3. No HTTP administration endpoints are exposed because accepting caller-supplied identity fields would create a false security boundary.

## 14. What Phase 1 / Step 2 will add

Step 2 should connect authenticated identity to `TenantContext`, define session/token mechanics and revocation, add production route guards, and validate the same policy service against the selected identity provider. It should preserve the platform/tenant scope model and audit all authentication and authorization lifecycle events.
