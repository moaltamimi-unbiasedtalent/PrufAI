# ADR-086 Audio and transcript evidence precedence
Status: Accepted for MVP baseline

Context: Transcription can be imperfect and may be superseded.
Decision: Audio remains primary evidence. Transcript is derived evidence and
must be referenced by exact SpeechTranscriptionVersion and segment; audio and
transcript references never resolve through an unpinned "latest" value.
Consequences: Missing, incorrect, or stale transcription cannot rewrite the
recording or redirect historical evidence.
Alternatives considered: Transcript replacing audio, rejected.
