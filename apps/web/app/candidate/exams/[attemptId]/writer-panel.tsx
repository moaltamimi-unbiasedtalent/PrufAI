"use client";

import React, { useEffect, useId, useRef, useState } from "react";

export type WriterPhase = "checking" | "owned" | "held" | "unavailable";

export function WriterPanel({
  phase,
  generation,
  contextKey,
  pendingAnswers,
  onRetry,
  onTakeover,
}: {
  phase: WriterPhase;
  generation: number;
  contextKey: string | null;
  pendingAnswers: number;
  onRetry: () => void;
  onTakeover: (generation: number) => Promise<boolean>;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [expected, setExpected] = useState(0);
  const title = useId(),
    description = useId();
  useEffect(() => {
    dialog.current?.close();
    setChecked(false);
    setBusy(false);
  }, [contextKey]);
  useEffect(() => {
    if (phase === "owned") dialog.current?.close();
  }, [phase]);
  const close = () => {
    dialog.current?.close();
    setChecked(false);
    trigger.current?.focus();
  };
  return (
    <section lang="de" aria-label="Bearbeitungsfreigabe">
      <p role="status" aria-atomic="true">
        {phase === "owned"
          ? "Die Bearbeitung ist für diesen Browser freigegeben."
          : phase === "checking"
            ? "Die Bearbeitungsfreigabe wird beim Server geprüft…"
            : phase === "held"
              ? "Diese Übung ist für einen anderen Browser reserviert oder muss erneut bestätigt werden. Antworten und Abgabe sind hier gesperrt."
              : "Die Bearbeitungsfreigabe konnte nicht bestätigt werden. Prüfen Sie die Verbindung und versuchen Sie es erneut."}
      </p>
      {phase === "unavailable" && (
        <button type="button" onClick={onRetry}>
          Bearbeitungsstatus erneut prüfen
        </button>
      )}
      {phase === "held" && (
        <>
          <p>
            Hier sehen Sie den beim Server gespeicherten Stand. Antworten, die
            im anderen Browser noch nicht beim Server angekommen sind, können
            hier fehlen.
          </p>
          {pendingAnswers > 0 && (
            <p>
              {pendingAnswers} lokale Antworten bleiben unbestätigt auf diesem
              Gerät. Sie werden nicht verworfen. Nach einer Übernahme müssen sie
              mit den gespeicherten Antwortversionen abgeglichen werden.
            </p>
          )}
          <button
            ref={trigger}
            type="button"
            onClick={() => {
              setChecked(false);
              setExpected(generation);
              dialog.current?.showModal();
              cancel.current?.focus();
            }}
          >
            In diesem Browser übernehmen
          </button>
        </>
      )}
      <dialog
        ref={dialog}
        lang="de"
        aria-labelledby={title}
        aria-describedby={description}
        onCancel={close}
      >
        <h2 id={title}>Übung in diesem Browser fortsetzen?</h2>
        <p id={description}>
          Prüfen und sichern Sie offene Antworten zuerst im anderen Browser.
          Nach der Übernahme kann dieser andere Browser keine Antworten mehr
          speichern oder abgeben. Hier geht es mit dem beim Server gespeicherten
          Stand weiter. Noch nicht übertragene Antworten aus dem anderen Browser
          werden nicht automatisch übernommen. Lokale unbestätigte Antworten auf
          diesem Gerät bleiben erhalten und werden anhand ihrer ursprünglichen
          Antwortversion abgeglichen.
        </p>
        <label>
          <input
            type="checkbox"
            checked={checked}
            disabled={busy}
            onChange={(e) => setChecked(e.target.checked)}
          />
          Ich habe offene Antworten geprüft und möchte hier mit dem
          gespeicherten Stand fortfahren.
        </label>
        <div>
          <button ref={cancel} type="button" disabled={busy} onClick={close}>
            Abbrechen
          </button>
          <button
            type="button"
            disabled={!checked || busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onTakeover(expected);
              } finally {
                setBusy(false);
                close();
              }
            }}
          >
            {busy
              ? "Übernahme wird geprüft…"
              : "Übernehmen und Antworten prüfen"}
          </button>
        </div>
      </dialog>
    </section>
  );
}
