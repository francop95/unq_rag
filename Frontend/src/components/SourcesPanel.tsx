import { useState } from "react";
import type { Source } from "../types/api";
import { sourceDocumentUrl, mediaUrl } from "../api/client";
import { CONTENT_TYPE_LABEL, kindOf, mediaPathOf, type MediaEntry } from "../lib/media";
import type { SourceSplit } from "../lib/sources";
import ChunkText from "./ChunkText";
import TableBlock from "./TableBlock";

interface Props {
  split: SourceSplit;
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
              {/* El glosario no es una página de la planilla sino su resumen
                  completo, y se pasa en todas las consultas: decir "pág. 1"
                  mandaría a buscar algo que no está ahí. */}
              <span>{source.is_glossary ? "nomenclatura del equipo" : `pág. ${String(source.page)}`}</span>
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
        {/* Qué dijo haber leído ahí. Es lo que convierte la cita en algo
            comprobable: sin esto, un plano listado solo afirma "lo usé". */}
        <span className="block font-mono text-[0.68rem] text-muted-2">
          {source.text?.trim()
            ? source.text.trim()
            : source.citado
              ? "plano completo · adjuntado al modelo"
              : "adjuntado al modelo · no declaró haberlo usado"}
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

export default function SourcesPanel({ split, mediaEntries, onOpenMedia }: Props) {
  const [collapsed, setCollapsed] = useState(false);
  // El contexto no citado arranca plegado: está para poder auditarlo, no para
  // competir por atención con las fuentes de las que salió la respuesta.
  const [verResto, setVerResto] = useState(false);

  const { citadas, resto, planos, declaradas } = split;
  if (citadas.length + resto.length + planos.length === 0) return null;

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
          {citadas.length > 0 && `${citadas.length} ${citadas.length === 1 ? "fragmento" : "fragmentos"}`}
          {citadas.length > 0 && planos.length > 0 && " · "}
          {planos.length > 0 && `${planos.length} ${planos.length === 1 ? "plano" : "planos"}`}
          {resto.length > 0 && ` · +${resto.length} de contexto`}
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
          {citadas.length > 0 && (
            <div className="space-y-1.5">
              <SectionLabel count={citadas.length}>
                {declaradas ? "Fragmentos citados en la respuesta" : "Contexto recuperado"}
              </SectionLabel>
              {/* Sin declaración del modelo no se puede afirmar de dónde salió la
                  respuesta, solo qué tuvo a la vista. Decirlo es más útil que
                  presentar lo recuperado como si fuera la cita. */}
              {!declaradas && (
                <p className="text-[0.68rem] leading-snug text-muted-2">
                  El modelo no declaró qué fragmentos usó; estos son los más relevantes
                  de los que recibió.
                </p>
              )}
              {citadas.map((source, i) => (
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

          {planos.length > 0 && (
            <div className="space-y-1.5">
              <SectionLabel count={planos.length}>Planos de referencia</SectionLabel>
              {planos.map((source, i) => (
                <PlanCard key={i} source={source} />
              ))}
            </div>
          )}

          {resto.length > 0 && (
            <div className="space-y-1.5 border-t border-border/50 pt-3">
              <button
                type="button"
                onClick={() => setVerResto((v) => !v)}
                className="flex w-full items-center gap-2 text-left font-mono text-[0.68rem] text-muted-2 transition hover:text-text"
              >
                <svg
                  viewBox="0 0 12 12"
                  fill="none"
                  className={`h-3 w-3 transition-transform duration-200 ${verResto ? "rotate-180" : ""}`}
                >
                  <path d="M2.5 4.5L6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
                <span className="flex-1">
                  Ver todo el contexto recuperado ({resto.length}{" "}
                  {resto.length === 1 ? "fragmento más" : "fragmentos más"})
                </span>
              </button>

              {verResto && (
                <div className="animate-fade-in-up space-y-1.5 opacity-80">
                  <p className="text-[0.68rem] leading-snug text-muted-2">
                    Se recuperaron del índice y se le entregaron al modelo, pero no los
                    citó como respaldo de esta respuesta.
                  </p>
                  {resto.map((source, i) =>
                    source.is_attached_plan ? (
                      <PlanCard key={i} source={source} />
                    ) : (
                      <SourceCard
                        key={i}
                        source={source}
                        number={citadas.length + i + 1}
                        mediaEntries={mediaEntries}
                        onOpenMedia={onOpenMedia}
                      />
                    ),
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
