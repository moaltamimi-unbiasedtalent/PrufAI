# ADR-022: Knowledge Sources and Immutable Versions

Status: Accepted

Sources identify canonical standards. Published content is represented by
version rows with effective intervals, checksums, source URLs, and explicit
supersession. Approved versions are immutable and are the only candidates for
downstream retrieval. Current retrieval and historical as-of-date retrieval are
separate: superseded versions are excluded from current results but remain
eligible when their effective interval matches a historical query.
