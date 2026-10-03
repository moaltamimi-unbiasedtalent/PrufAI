# ADR-055: Memory tenant isolation

Platform-shared memory has no organisation identifier. Organisation-private
memory must carry the matching organisation identifier. Resolution always
filters scope against the trusted tenant context; arbitrary request fields do
not establish authority.
