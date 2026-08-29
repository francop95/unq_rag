import { mediaUrl } from "../api/client";
import { CONTENT_TYPE_LABEL, summarizeMedia, type MediaEntry } from "../lib/media";

interface Props {
  entries: MediaEntry[];
  onOpen: (index: number) => void;
}

function TableThumb() {
  return (
    <div className="flex h-full w-full flex-col justify-center gap-[3px] bg-surface-2 p-3">
      <div className="h-1.5 rounded-sm bg-accent-2/50" />
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex gap-[3px]">
          <div className="h-1.5 flex-1 rounded-sm bg-muted-2/30" />
          <div className="h-1.5 flex-1 rounded-sm bg-muted-2/20" />
          <div className="h-1.5 flex-1 rounded-sm bg-muted-2/25" />
        </div>
      ))}
    </div>
  );
}

/**
 * Tira de miniaturas con toda la evidencia visual de la respuesta, arriba del panel
 * de fuentes.
 *
 * Es el cambio que más importa: una consulta típica recupera 5-7 planos y tablas, y
 * antes cada uno vivía escondido detrás de su fila colapsada del panel, todas
 * idénticas entre sí. El usuario no tenía forma de saber que la respuesta traía
 * diagramas, y en un asistente de mantenimiento el diagrama suele ser LA respuesta.
 */
export default function MediaGallery({ entries, onOpen }: Props) {
  if (entries.length === 0) return null;

  return (
    <div className="mt-3">
      <div className="mb-2 flex items-center gap-2">
        <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5 text-accent-2">
          <rect x="1.75" y="2.75" width="12.5" height="10.5" rx="1.5" stroke="currentColor" strokeWidth="1.3" />
          <circle cx="5.75" cy="6.25" r="1.1" fill="currentColor" />
          <path d="M2 11.5l3.2-2.8 2.4 2.1 2.6-2.6L14 11.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <p className="font-mono text-[0.7rem] uppercase tracking-wide text-muted">
          Material visual
        </p>
        <span className="font-mono text-[0.7rem] text-muted-2">{summarizeMedia(entries)}</span>
      </div>

      {/* La tira scrollea en horizontal: el degradado del borde derecho es lo que
          avisa que hay más material más allá del corte. */}
      <div className="relative">
        <div className="flex gap-2.5 overflow-x-auto pb-2 pr-6">
        {entries.map((entry, i) => (
          <button
            key={entry.path}
            type="button"
            onClick={() => onOpen(i)}
            title={`${CONTENT_TYPE_LABEL[entry.media.content_type || ""] || "Adjunto"} — ${entry.fileName}, pág. ${entry.page}`}
            className="group relative w-36 shrink-0 overflow-hidden rounded-xl border border-border bg-surface-2 text-left transition duration-200 hover:-translate-y-0.5 hover:border-accent-2/60 hover:shadow-lg hover:shadow-accent-2/10"
          >
            <div className="flex h-24 w-full items-center justify-center overflow-hidden bg-white">
              {entry.kind === "image" ? (
                // object-contain, no cover: en un diagrama recortar los bordes tapa
                // justo las etiquetas de bornes y el marco de la figura.
                <img
                  src={mediaUrl(entry.path)}
                  alt=""
                  loading="lazy"
                  className="crisp-lineart h-full w-full object-contain p-1 transition duration-300 group-hover:scale-105"
                />
              ) : (
                <TableThumb />
              )}
            </div>

            <div className="flex items-center gap-1.5 px-2.5 py-2">
              <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-surface-3 font-mono text-[0.6rem] text-muted">
                {entry.sourceNumber}
              </span>
              <span className="min-w-0 flex-1 truncate font-mono text-[0.65rem] text-muted">
                pág. {String(entry.page)}
              </span>
            </div>

            {/* Lupa al hacer hover: comunica que se puede ampliar */}
            <span className="pointer-events-none absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-bg/80 text-accent-2 opacity-0 backdrop-blur-sm transition duration-200 group-hover:opacity-100">
              <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5">
                <circle cx="7" cy="7" r="4.25" stroke="currentColor" strokeWidth="1.4" />
                <path d="M10.2 10.2L14 14M7 5.2v3.6M5.2 7h3.6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
            </span>
          </button>
        ))}
        </div>

        {entries.length > 4 && (
          <span className="pointer-events-none absolute right-0 top-0 h-full w-10 bg-gradient-to-l from-bg to-transparent" />
        )}
      </div>
    </div>
  );
}
