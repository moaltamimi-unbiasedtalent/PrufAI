# ADR-023: Ingestion and Page Provenance

Status: Accepted

Ingestion is parser-driven and asynchronous-ready: a durable job records
status, parser identity, page count, and safe failure details. Each page stores
normalized text, content hash, extraction status, and the originating job so
future retrieval can cite the exact source page.
