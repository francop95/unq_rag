import { useCallback, useEffect, useState } from "react";
import { mediaUrl, sourceDocumentUrl } from "../api/client";
import { CONTENT_TYPE_LABEL, type MediaEntry } from "../lib/media";
import TableBlock from "./TableBlock";

interface Props {
  entries: MediaEntry[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
}

/**
 * Visor a pantalla completa de la evidencia (planos, diagramas, fotos y tablas).
 * Los planos técnicos se leen ampliados, así que el zoom y el paneo no son un lujo:
 * a 224px de alto en la tarjeta no se distingue una etiqueta de borne.
 */
export default function Lightbox({ entries, index, onClose, onNavigate }: Props) {
  const [zoomed, setZoomed] = useState(false);
  const entry = entries[index];

  const go = useCallback(
    (delta: number) => {
      const next = (index + delta + entries.length) % entries.length;
      setZoomed(false);
      onNavigate(next);
    },
    [index, entries.length, onNavigate],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") go(1);
      if (e.key === "ArrowLeft") go(-1);
    }
    window.addEventListener("keydown", onKey);
    // Mientras el visor está abierto el fondo no debe scrollear.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [go, onClose]);

  if (!entry) return null;

  const label = CONTENT_TYPE_LABEL[entry.media.content_type || ""] || "Adjunto";

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-bg/97 backdrop-blur-lg">
      {/* Barra superior: qué se está viendo y de dónde salió */}
      <div className="flex shrink-0 items-center gap-3 border-b border-white/10 px-4 py-3">
        <span className="rounded border border-accent-2/40 bg-accent-2-soft px-2 py-0.5 font-mono text-[0.65rem] uppercase tracking-wide text-accent-2">
          {label}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-xs text-white/90">{entry.fileName}</p>
          <p className="font-mono text-[0.7rem] text-white/50">
            pág. {String(entry.page)} · fuente {entry.sourceNumber}
          </p>
        </div>

        {entries.length > 1 && (
          <span className="shrink-0 font-mono text-[0.7rem] tabular-nums text-white/50">
            {index + 1} / {entries.length}
          </span>
        )}

        {entry.kind === "image" && (
          <button
            type="button"
            onClick={() => setZoomed((v) => !v)}
            title={zoomed ? "Ajustar a pantalla" : "Ampliar al tamaño original"}
            className="shrink-0 rounded-lg border border-white/15 p-2 text-white/70 transition hover:bg-white/10 hover:text-white"
          >
            <svg viewBox="0 0 20 20" fill="none" className="h-4 w-4">
              {zoomed ? (
                <path d="M7.5 3v4.5H3M12.5 17v-4.5H17M3 12.5h4.5V17M17 7.5h-4.5V3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              ) : (
                <path d="M3 7.5V3h4.5M17 12.5V17h-4.5M12.5 3H17v4.5M7.5 17H3v-4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              )}
            </svg>
          </button>
        )}

        <a
          href={sourceDocumentUrl(entry.fileName, entry.page)}
          target="_blank"
          rel="noopener noreferrer"
          title={`Abrir ${entry.fileName} en la página ${entry.page}`}
          className="shrink-0 rounded-lg border border-white/15 p-2 text-white/70 transition hover:bg-white/10 hover:text-white"
        >
          <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
            <path d="M6.5 3.5H3.5A1.5 1.5 0 0 0 2 5v7.5A1.5 1.5 0 0 0 3.5 14H11a1.5 1.5 0 0 0 1.5-1.5V9.5M9.5 2H14v4.5M14 2 7 9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </a>

        <button
          type="button"
          onClick={onClose}
          title="Cerrar (Esc)"
          className="shrink-0 rounded-lg border border-white/15 p-2 text-white/70 transition hover:bg-white/10 hover:text-white"
        >
          <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
            <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </div>

      {/* Contenido */}
      <div
        className={`relative flex-1 ${zoomed ? "overflow-auto" : "flex items-center justify-center overflow-hidden"} p-4`}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        {entry.kind === "image" ? (
          <img
            src={mediaUrl(entry.path)}
            alt={`${label} — ${entry.fileName} página ${entry.page}`}
            onClick={() => setZoomed((v) => !v)}
            className={`crisp-lineart ${
              zoomed
                ? "max-w-none cursor-zoom-out rounded-lg bg-white"
                : "max-h-full max-w-full cursor-zoom-in rounded-lg bg-white object-contain shadow-2xl"
            }`}
          />
        ) : (
          // Las tablas se alinean arriba y usan todo el ancho disponible: centradas
          // verticalmente, una tabla de 2 filas quedaba flotando en medio del vacío.
          <div className="max-h-full w-full max-w-5xl self-start overflow-auto rounded-xl border border-border bg-surface p-4">
            <TableBlock mediaPath={entry.path} />
          </div>
        )}

        {entries.length > 1 && (
          <>
            <button
              type="button"
              onClick={() => go(-1)}
              aria-label="Anterior"
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full border border-white/15 bg-black/50 p-2.5 text-white/70 transition hover:bg-black/80 hover:text-white"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
                <path d="M10 3L5 8l5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              type="button"
              onClick={() => go(1)}
              aria-label="Siguiente"
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full border border-white/15 bg-black/50 p-2.5 text-white/70 transition hover:bg-black/80 hover:text-white"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
                <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </>
        )}
      </div>

      <p className="shrink-0 border-t border-white/10 px-4 py-2 text-center font-mono text-[0.65rem] text-white/40">
        {entries.length > 1 ? "← → para navegar · " : ""}Esc para cerrar
        {entry.kind === "image" ? " · clic en la imagen para zoom" : ""}
      </p>
    </div>
  );
}
