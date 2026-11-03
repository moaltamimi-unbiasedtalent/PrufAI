"use client";

import React, { useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth";
import { AudioUrlLease, fetchPrivateAudio } from "../lib/private-audio";

export function PrivateAudioPlayer({
  path,
  maximumBytes,
  label = "Stored candidate recording",
}: {
  path: string;
  maximumBytes: number;
  label?: string;
}) {
  const { user, organisationId } = useAuth();
  const context =
    user && organisationId ? `${user.id}:${organisationId}:${path}` : null;
  const current = useRef(context);
  current.current = context;
  const [load, setLoad] = useState(0);
  const [snapshot, setSnapshot] = useState<{
    context: string;
    url: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const audio = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setLoading(false);
    if (!context || !load) return;
    const abort = new AbortController();
    const lease = new AudioUrlLease();
    const element = audio.current;
    setLoading(true);
    void fetchPrivateAudio(path, abort.signal, maximumBytes)
      .then((blob) => {
        if (abort.signal.aborted || current.current !== context) return;
        setSnapshot({ context, url: lease.replace(blob) });
      })
      .catch((cause: unknown) => {
        if (!abort.signal.aborted && current.current === context)
          setError(
            cause instanceof Error
              ? cause.message
              : "The recording could not be loaded.",
          );
      })
      .finally(() => {
        if (!abort.signal.aborted && current.current === context)
          setLoading(false);
      });
    return () => {
      abort.abort();
      if (element) {
        element.pause();
        element.removeAttribute("src");
        element.load();
      }
      lease.clear();
    };
  }, [context, path, maximumBytes, load]);

  const url = snapshot?.context === context ? snapshot.url : null;
  return (
    <section
      aria-label="Private audio playback"
      style={{ overflowWrap: "anywhere" }}
    >
      <h3>Stored recording playback</h3>
      <p>
        The complete authorized recording is buffered privately in this browser
        for playback.
      </p>
      {loading && <p role="status">Loading stored audio…</p>}
      {error && <p role="alert">{error}</p>}
      <audio
        ref={audio}
        aria-label={label}
        controls
        preload="metadata"
        src={url ?? undefined}
        hidden={!url}
        style={{ width: "100%", maxWidth: 600 }}
        onError={() => {
          if (url)
            setError(
              "The browser could not decode the stored audio. Retry or contact your institution.",
            );
        }}
      />
      <button
        type="button"
        disabled={!context || loading}
        onClick={() => setLoad((value) => value + 1)}
      >
        {url || error ? "Reload stored audio" : "Load stored audio"}
      </button>
    </section>
  );
}
