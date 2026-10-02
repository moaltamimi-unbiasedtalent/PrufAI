# ADR-046: Certification authorization guard

The generic engine refuses `OFFICIAL_CERTIFICATION` requests with a blocking, auditable failure. This is a platform authorization boundary and is not a provider-specific rule implementation.
