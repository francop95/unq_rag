import { useState } from "react";
import type { Source } from "../types/api";
import { sourceDocumentUrl, mediaUrl } from "../api/client";
import { CONTENT_TYPE_LABEL, kindOf, mediaPathOf, type MediaEntry } from "../lib/media";
import ChunkText from "./ChunkText";
import TableBlock from "./TableBlock";

interface Props {
  sources: Source[];
  mediaEntries: MediaEntry[];
  onOpenMedia: (index: number) => void;
}

/** Barra de confianza: verde alta, ámbar media, rojo baja. */
function ConfidenceBar({ score }: { score: number }) {
  // score llega normalizado 0-100 (ver ChromaConnection._normalize_display_score).
  const pct = Math.max(3, Math.min(100, score));
  const color = pct >= 70 ? "bg-success" : pct >= 50 ? "bg-accent" : "bg-danger";
  return (
    <div className="flex shrink-0 items-center gap-1.5" title={`Similitud con la pregunta: ${score.toFixed(0)}%`}>
      <span className="font-mono text-[0.68rem] tabular-nums text-muted-2">{score.toFixed(0)}%</span>
      <span className="h-1 w-10 overflow-hidden rounded-full bg-surface-3">
        <span className={`block h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </span>
    </div>
  );
}

function OpenPdfLink({ fileName, page }: { fileName: string; page: string | number }) {
  return (
    <a
      href={sourceDocumentUrl(fileName, page)}
      target="_blank"
      rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()}
      title={`Abrir ${fileName} en la página ${page}`}
      className="shrink-0 rounded-md p-1.5 text-muted-2 transition hover:bg-surface-3 hover:text-accent-2"
    >
      <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5">
        <path
          d="M6.5 3.5H3.5A1.5 1.5 0 0 0 2 5v7.5A1.5 1.5 0 0 0 3.5 14H11a1.5 1.5 0 0 0 1.5-1.5V9.5M9.5 2H14v4.5M14 2 7 9"
          stroke="currentColor"
          strokeWidth="1.3"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </a>
  );
}

function SourceCard({
  source,
  number,
  mediaEntries,
  onOpenMedia,
}: {
  source: Source;
  number: number;
  mediaEntries: MediaEntry[];
  onOpenMedia: (index: number) => void;
}) {
  const [open, setOpen] = useState(false);
  const media = source.media || [];
  const hasText = Boolean(source.text && source.text.trim());
  const hasContent = media.length > 0 || hasText;
  // Si la evidencia es una tabla, la tabla renderizada es la versión canónica del
  // fragmento; el texto del chunk solo aporta el contexto de alrededor.
  const hasTableMedia = media.some((m) => kindOf(m) === "table");
  const typeLabels = media
    .map((m) => CONTENT_TYPE_LABEL[m.content_type || ""])
    .filter(Boolean) as string[];

  return (
    <div
      className={`overflow-hidden rounded-xl border transition-colors ${
        open ? "border-accent-2/40 bg-surface-2" : "border-border bg-surface-2/50 hover:border-border/80"
      }`}
    >
      <div className="flex items-center gap-2.5 px-3 py-2.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          disabled={!hasContent}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left disabled:cursor-default"
        >
          <span
            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full font-mono text-[0.62rem] transition-colors ${
              open ? "bg-accent-2 text-bg" : "bg-surface-3 text-muted"
            }`}
          >
            {number}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-mono text-xs text-text">
              {source.file_name || "Documento"}
            </span>
            <span className="mt-0.5 flex items-center gap-1.5 font-mono text-[0.68rem] text-muted-2">
              <span>pág. {String(source.page)}</span>
              {typeLabels.map((label) => (
                <span
                  key={label}
                  className="rounded border border-accent-2/30 bg-accent-2-soft px-1.5 py-px text-[0.6rem] text-accent-2"
                >
                  {label}
                </span>
              ))}
            </span>
          </span>
        </button>

        <ConfidenceBar score={source.similarity_score} />
        {source.file_name && <OpenPdfLink fileName={source.file_name} page={source.page} />}

        {hasContent && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? "Contraer fuente" : "Expandir fuente"}
            className="shrink-0 rounded-md p-1.5 text-muted-2 transition hover:bg-surface-3 hover:text-text"
          >
            <svg
              viewBox="0 0 12 12"
              fill="none"
              className={`h-3.5 w-3.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
            >
              <path d="M2.5 4.5L6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>

      {open && hasContent && (
        <div className="animate-fade-in-up space-y-3 border-t border-border/70 bg-bg/40 p-3">
          {hasText && <ChunkText text={source.text!} omitRelevant={hasTableMedia} />}

          {media.map((item, i) => {
            const path = mediaPathOf(item);
            const kind = kindOf(item);
            if (!path || !kind) return null;

            if (kind === "table") return <TableBlock key={i} mediaPath={path} />;

            const galleryIndex = mediaEntries.findIndex((e) => e.path === path);
            return (
              <button
                key={i}
                type="button"
                onClick={() => galleryIndex >= 0 && onOpenMedia(galleryIndex)}
                className="group block w-full overflow-hidden rounded-lg border border-border bg-white/95 transition hover:border-accent-2/60"
                title="Ampliar"
              >
                <img
                  src={mediaUrl(path)}
                  alt={`${source.file_name} página ${source.page}`}
                  loading="lazy"
                  className="crisp-lineart max-h-64 w-full object-contain transition duration-300 group-hover:scale-[1.02]"
                />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Plano adjuntado siempre al modelo: no tiene score ni chunk, solo se abre. */
function PlanCard({ source }: { source: Source }) {
  return (
    <a
      href={sourceDocumentUrl(source.file_name, source.page)}
      target="_blank"
      rel="noopener noreferrer"
      className="group flex items-center gap-2.5 rounded-xl border border-accent/25 bg-accent-soft/40 px-3 py-2.5 transition hover:border-accent/50 hover:bg-accent-soft"
    >
      <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg border border-accent/30 bg-accent-soft text-accent">
        <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5">
          <path d="M2 3.5h12v9H2z" stroke="currentColor" strokeWidth="1.2" />
          <path d="M2 6.5h12M5.5 3.5v9M10.5 6.5v6" stroke="currentColor" strokeWidth="1.2" />
        </svg>
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate font-mono text-xs text-text">{source.file_name}</span>
        <span className="block font-mono text-[0.68rem] text-muted-2">
          plano completo · adjuntado al modelo
        </span>
      </span>
      <span className="shrink-0 font-mono text-[0.62rem] uppercase tracking-wide text-accent opacity-60 transition group-hover:opacity-100">
        abrir ↗
      </span>
    </a>
  );
}

function SectionLabel({ children, count }: { children: React.ReactNode; count: number }) {
  return (
    <p className="flex items-center gap-2 font-mono text-[0.7rem] uppercase tracking-wide text-muted">
      {children}
      <span className="rounded-full bg-surface-3 px-1.5 py-px text-[0.6rem] text-muted-2">{count}</span>
    </p>
  );
}

export default function SourcesPanel({ sources, mediaEntries, onOpenMedia }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  if (!sources || sources.length === 0) return null;

  // Los planos se separan de los chunks recuperados: no compiten por relevancia (se
  // adjuntan siempre) y mezclarlos hacía que un plano con 0% se leyera como la peor
  // fuente de la lista, cuando muchas veces es de donde sale la respuesta.
  const chunks = sources.filter((s) => !s.is_attached_plan);
  const plans = sources.filter((s) => s.is_attached_plan);

  return (
    <div className="mt-3 rounded-xl border border-border/70 bg-surface/40 p-3">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center gap-2 text-left"
      >
        <svg viewBox="0 0 16 16" fill="none" className="h-3.5 w-3.5 text-muted">
          <path d="M2 4h12M2 8h12M2 12h8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
        </svg>
        <span className="flex-1 font-mono text-[0.7rem] uppercase tracking-wide text-muted">
          De dónde salió esta respuesta
        </span>
        <span className="font-mono text-[0.68rem] text-muted-2">
          {chunks.length > 0 && `${chunks.length} ${chunks.length === 1 ? "fragmento" : "fragmentos"}`}
          {chunks.length > 0 && plans.length > 0 && " · "}
          {plans.length > 0 && `${plans.length} ${plans.length === 1 ? "plano" : "planos"}`}
        </span>
        <svg
          viewBox="0 0 12 12"
          fill="none"
          className={`h-3.5 w-3.5 text-muted-2 transition-transform duration-200 ${collapsed ? "" : "rotate-180"}`}
        >
          <path d="M2.5 4.5L6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </button>

      {!collapsed && (
        <div className="mt-3 space-y-3">
          {chunks.length > 0 && (
            <div className="space-y-1.5">
              <SectionLabel count={chunks.length}>Fragmentos del manual</SectionLabel>
              {chunks.map((source, i) => (
                <SourceCard
                  key={i}
                  source={source}
                  number={i + 1}
                  mediaEntries={mediaEntries}
                  onOpenMedia={onOpenMedia}
                />
              ))}
            </div>
          )}

          {plans.length > 0 && (
            <div className="space-y-1.5">
              <SectionLabel count={plans.length}>Planos de referencia</SectionLabel>
              {plans.map((source, i) => (
                <PlanCard key={i} source={source} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
