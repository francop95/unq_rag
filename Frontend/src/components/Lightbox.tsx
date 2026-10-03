import { useCallback, useEffect, useRef, useState } from "react";
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
// Escala mínima y máxima, relativas al ajuste a pantalla. El máximo es alto
// porque 308 de los 1074 recortes del índice miden menos de 1000 px de ancho y
// alguno 93: sin poder pasar del tamaño natural, esos son ilegibles.
const ESCALA_MIN = 0.5;
const ESCALA_MAX = 8;
const PASO = 1.35;

export default function Lightbox({ entries, index, onClose, onNavigate }: Props) {
  // 1 = ajustada a la pantalla. El zoom multiplica desde ahí, no desde el tamaño
  // del archivo: así una imagen chica también se agranda, que es lo que el
  // botón anterior no hacía (con `max-w-none`, ajustada y "ampliada" eran la
  // misma cosa para cualquier imagen más chica que la ventana).
  const [escala, setEscala] = useState(1);
  const [desplazamiento, setDesplazamiento] = useState({ x: 0, y: 0 });
  const [arrastrando, setArrastrando] = useState(false);
  const inicioArrastre = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const entry = entries[index];
  const ampliada = escala > 1.001;

  const reencuadrar = useCallback(() => {
    setEscala(1);
    setDesplazamiento({ x: 0, y: 0 });
  }, []);

  const cambiarEscala = useCallback((factor: number) => {
    setEscala((e) => {
      const nueva = Math.min(ESCALA_MAX, Math.max(ESCALA_MIN, e * factor));
      // Al volver al ajuste se recentra: si no, queda del tamaño correcto pero corrida.
      if (Math.abs(nueva - 1) < 0.01) setDesplazamiento({ x: 0, y: 0 });
      return nueva;
    });
  }, []);

  const go = useCallback(
    (delta: number) => {
      const next = (index + delta + entries.length) % entries.length;
      reencuadrar();
      onNavigate(next);
    },
    [index, entries.length, onNavigate, reencuadrar],
  );

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") go(1);
      if (e.key === "ArrowLeft") go(-1);
      if (e.key === "+" || e.key === "=") cambiarEscala(PASO);
      if (e.key === "-" || e.key === "_") cambiarEscala(1 / PASO);
      if (e.key === "0") reencuadrar();
    }
    window.addEventListener("keydown", onKey);
    // Mientras el visor está abierto el fondo no debe scrollear.
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
    };
  }, [go, onClose, cambiarEscala, reencuadrar]);

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
          <div className="flex shrink-0 items-center gap-1 rounded-lg border border-white/15">
            <button
              type="button"
              onClick={() => cambiarEscala(1 / PASO)}
              disabled={escala <= ESCALA_MIN + 0.001}
              title="Alejar (−)"
              className="rounded-l-lg p-2 text-white/70 transition hover:bg-white/10 hover:text-white disabled:opacity-30 disabled:hover:bg-transparent"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
                <path d="M3.5 8h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
            {/* El porcentaje, además de los botones: sin él no se sabe si el
                clic hizo algo, que es exactamente lo que fallaba con las
                imágenes chicas. Y sirve de botón para volver a ajustar. */}
            <button
              type="button"
              onClick={reencuadrar}
              title="Ajustar a pantalla (0)"
              className="min-w-[3.4rem] px-1 py-2 font-mono text-[0.7rem] tabular-nums text-white/70 transition hover:text-white"
            >
              {Math.round(escala * 100)}%
            </button>
            <button
              type="button"
              onClick={() => cambiarEscala(PASO)}
              disabled={escala >= ESCALA_MAX - 0.001}
              title="Acercar (+)"
              className="rounded-r-lg p-2 text-white/70 transition hover:bg-white/10 hover:text-white disabled:opacity-30 disabled:hover:bg-transparent"
            >
              <svg viewBox="0 0 16 16" fill="none" className="h-4 w-4">
                <path d="M8 3.5v9M3.5 8h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
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
        className="relative flex flex-1 items-center justify-center overflow-hidden p-4"
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
        onWheel={(e) => {
          if (entry.kind !== "image") return;
          // Solo con Ctrl/Cmd, como en cualquier visor: una rueda que hace zoom
          // sin modificador secuestra el gesto de scrollear la página.
          if (!e.ctrlKey && !e.metaKey) return;
          cambiarEscala(e.deltaY < 0 ? PASO : 1 / PASO);
        }}
      >
        {entry.kind === "image" ? (
          <img
            src={mediaUrl(entry.path)}
            alt={`${label} — ${entry.fileName} página ${entry.page}`}
            draggable={false}
            onDoubleClick={() => (ampliada ? reencuadrar() : cambiarEscala(PASO * PASO))}
            onPointerDown={(e) => {
              if (!ampliada) return;
              (e.target as HTMLElement).setPointerCapture(e.pointerId);
              inicioArrastre.current = {
                x: e.clientX, y: e.clientY,
                dx: desplazamiento.x, dy: desplazamiento.y,
              };
              setArrastrando(true);
            }}
            onPointerMove={(e) => {
              const a = inicioArrastre.current;
              if (!a) return;
              setDesplazamiento({
                x: a.dx + (e.clientX - a.x),
                y: a.dy + (e.clientY - a.y),
              });
            }}
            onPointerUp={() => { inicioArrastre.current = null; setArrastrando(false); }}
            onPointerCancel={() => { inicioArrastre.current = null; setArrastrando(false); }}
            style={{
              // `object-contain` nunca agranda, solo achica, así que el ajuste
              // base deja chico un recorte de 400 px. El `scale` va encima y sí
              // agranda: es lo que permite leer una imagen pequeña, que es
              // justo lo que el botón anterior no hacía.
              transform: `translate(${desplazamiento.x}px, ${desplazamiento.y}px) scale(${escala})`,
              transition: arrastrando ? "none" : "transform .14s ease-out",
              cursor: ampliada ? (arrastrando ? "grabbing" : "grab") : "zoom-in",
            }}
            className="crisp-lineart max-h-full max-w-full rounded-lg bg-white object-contain shadow-2xl"
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
